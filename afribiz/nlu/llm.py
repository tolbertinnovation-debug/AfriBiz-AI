"""Claude as the understanding layer for messages the rules can't parse.

Claude never writes to the database directly: it calls the same validated actions as the rule
engine, through tools. The model handles the open-ended part (mixed languages, voice
transcripts with filler words, "how am I doing?" questions, business advice grounded in the
owner's own numbers). The bookkeeping stays deterministic and auditable.

History design: inside one inbound message the tool loop is append-only and passes
`response.content` back unchanged. Across messages we replay only the plain text of recent
turns (no thinking blocks), so earlier turns can be dropped freely without editing history the
model reasoned over.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from ..actions import Context, execute
from ..nlu.intents import Intent, LineSpec
from ..replies import Reply

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6

SYSTEM_PROMPT = """You are AfriBiz, a friendly business assistant on WhatsApp for small and informal businesses in Africa: \
market traders, kiosks/dukas, tailors, salons, food sellers, mechanics, farmers.

How to work:
- Owners write short, informal messages, often mixing English with Swahili, Pidgin, French, Yoruba, Hausa, Twi, \
Zulu, Amharic or other languages, and voice notes arrive as rough transcripts. Work out what they mean.
- When a message reports business activity, record it with the tools: sales (cash or credit), expenses, stock \
bought, money paid by customers. Use one tool call per distinct event; several events in one message are common.
- Amounts are in the business's own currency (given below) unless the owner names another. "1.5k" = 1500, \
"2 elfu" = 2000, "500 bob" = 500. Never invent prices or quantities: if a sale has no price and the item has no \
known price, the tool will say so and you should ask.
- Money the owner takes for personal or family use is an owner's drawing: record_expense with personal=true.
- Buying stock to resell is record_stock_purchase, not an expense.
- If you are unsure what the owner meant and a wrong record would matter, ask one short question instead of guessing.
- For questions about how the business is doing, call the read tools (get_report, get_stock, get_debtors, \
get_credit_profile) and base your advice on those numbers.
- For general business questions, give practical, low-cost advice that fits an informal business. You may \
call get_lesson to send a matching lesson.
- You cannot move money, send messages to customers yourself, or give regulated financial advice. Don't \
promise loans: the credit profile is an indicator, not an approval.

Replying:
- The owner sees every tool result verbatim, so do NOT repeat the numbers or confirmations from tool results.
- After tools, add at most one or two short sentences, and only when useful: a question, a warning or one tip. \
If there is nothing to add, reply with exactly: OK
- If no tool was needed, answer in at most 5 short lines.
- Reply in the owner's language (match the message; default given below). Use WhatsApp formatting (*bold*), \
no tables, no markdown headings."""


def _line_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "item": {"type": "string", "description": "Item name as the owner calls it, singular (e.g. 'bread', 'sugar 1kg')."},
            "quantity": {"type": "number", "description": "How many. Default 1."},
            "unit_price": {"type": "number", "description": "Price per single item, if stated."},
            "line_total": {"type": "number", "description": "Total for this line, if the owner stated a total instead of a unit price."},
        },
        "required": ["item"],
        "additionalProperties": False,
    }


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


PAYMENT_METHODS = ["cash", "mobile_money", "bank"]

TOOLS: list[dict[str, Any]] = [
    _tool(
        "record_sale",
        "Record a sale of one or more items. Use on_credit=true when the customer will pay later (deni/credit); "
        "amount_paid for a part payment now. A customer name is required for credit or part-paid sales.",
        {
            "lines": {"type": "array", "items": _line_schema()},
            "customer": {"type": "string", "description": "Customer name, if mentioned."},
            "on_credit": {"type": "boolean"},
            "amount_paid": {"type": "number", "description": "Amount paid now, for part payments."},
            "total": {"type": "number", "description": "Agreed total for the whole sale if it differs from the line sum (bundle/discount)."},
            "payment_method": {"type": "string", "enum": PAYMENT_METHODS},
        },
        ["lines"],
    ),
    _tool(
        "record_expense",
        "Record money spent running the business (rent, transport, wages, electricity, airtime, repairs, fees...). "
        "Set personal=true when the owner took money for personal/family use.",
        {
            "amount": {"type": "number"},
            "description": {"type": "string", "description": "Short description, e.g. 'transport to market'."},
            "category": {"type": "string", "enum": ["rent", "transport", "wages", "utilities", "airtime", "supplies",
                                                     "repairs", "fees", "marketing", "food", "loan_repayment",
                                                     "mobile_money_fees", "other"]},
            "personal": {"type": "boolean"},
            "payment_method": {"type": "string", "enum": PAYMENT_METHODS},
        },
        ["amount", "description"],
    ),
    _tool(
        "record_stock_purchase",
        "Record stock bought for resale (adds to inventory). Give either unit_cost or total_cost when known.",
        {
            "item": {"type": "string"},
            "quantity": {"type": "number"},
            "unit": {"type": "string", "description": "e.g. bags, kg, crates, pcs"},
            "unit_cost": {"type": "number"},
            "total_cost": {"type": "number"},
            "selling_price": {"type": "number", "description": "Planned selling price per unit, if mentioned."},
            "supplier": {"type": "string"},
        },
        ["item", "quantity"],
    ),
    _tool(
        "record_customer_payment",
        "Record money a customer paid towards what they owe (debt repayment).",
        {
            "customer": {"type": "string"},
            "amount": {"type": "number"},
            "payment_method": {"type": "string", "enum": PAYMENT_METHODS},
        },
        ["customer", "amount"],
    ),
    _tool(
        "create_invoice",
        "Create an invoice for a customer (recorded as a credit sale) and produce a PDF to forward.",
        {
            "customer": {"type": "string"},
            "lines": {"type": "array", "items": _line_schema()},
            "due_in_days": {"type": "integer"},
            "customer_phone": {"type": "string"},
        },
        ["customer", "lines"],
    ),
    _tool("get_report", "Sales, costs, profit and cash summary for a period.",
          {"period": {"type": "string", "enum": ["today", "yesterday", "week", "month", "all"]}}, ["period"]),
    _tool("get_stock", "Stock levels; optionally one item, or only items running low.",
          {"item": {"type": "string"}, "low_only": {"type": "boolean"}}, []),
    _tool("get_debtors", "List customers who owe money, with balances.", {}, []),
    _tool("draft_reminders", "Prepare polite payment reminders (with one-tap WhatsApp links) for debtors.",
          {"customer": {"type": "string", "description": "Only this customer, if named."}}, []),
    _tool("set_item_price", "Set the selling price of an item.",
          {"item": {"type": "string"}, "price": {"type": "number"}}, ["item", "price"]),
    _tool("count_stock", "Record a physical stock count for an item.",
          {"item": {"type": "string"}, "quantity": {"type": "number"}}, ["item", "quantity"]),
    _tool("set_reorder_level", "Warn the owner when an item falls to this quantity.",
          {"item": {"type": "string"}, "level": {"type": "number"}}, ["item", "level"]),
    _tool("save_customer_phone", "Save a customer's phone number.",
          {"customer": {"type": "string"}, "phone": {"type": "string"}}, ["customer", "phone"]),
    _tool("get_credit_profile", "The owner's credit profile from mobile money history: score, factors, affordable loan.", {}, []),
    _tool("get_lesson", "Send a short business lesson. topic: e.g. pricing, stock, credit, savings, marketing, fraud, records.",
          {"topic": {"type": "string"}}, []),
    _tool("undo_last_entry", "Remove the most recent record (when the owner says it was a mistake).", {}, []),
    _tool("get_recent_records", "The last 10 records, to check what was entered.", {}, []),
]


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _lines(raw: list[dict[str, Any]] | None) -> list[LineSpec]:
    out = []
    for r in raw or []:
        if not r.get("item"):
            continue
        if r.get("unit_price") is not None:
            price, kind = _dec(r["unit_price"]), "unit"
        elif r.get("line_total") is not None:
            price, kind = _dec(r["line_total"]), "total"
        else:
            price, kind = None, "bare"
        out.append(LineSpec(name=str(r["item"]), quantity=float(r.get("quantity") or 1), price=price, price_kind=kind))
    return out


def tool_to_intent(name: str, args: dict[str, Any]) -> Intent:
    if name == "record_sale":
        return Intent("record_sale", {
            "lines": _lines(args.get("lines")), "customer": args.get("customer"),
            "on_credit": bool(args.get("on_credit")), "amount_paid": _dec(args.get("amount_paid")),
            "total": _dec(args.get("total")), "method": args.get("payment_method") or "cash",
        })
    if name == "record_expense":
        return Intent("record_expense", {
            "amount": _dec(args.get("amount")), "description": args.get("description") or "expense",
            "category": args.get("category") if args.get("category") not in (None, "other") else None,
            "personal": bool(args.get("personal")), "method": args.get("payment_method") or "cash",
        })
    if name == "record_stock_purchase":
        if args.get("unit_cost") is not None:
            price, kind = _dec(args["unit_cost"]), "unit"
        elif args.get("total_cost") is not None:
            price, kind = _dec(args["total_cost"]), "total"
        else:
            price, kind = None, "bare"
        line = LineSpec(name=str(args.get("item")), quantity=float(args.get("quantity") or 1), price=price,
                        price_kind=kind, unit=args.get("unit"))
        return Intent("record_purchase", {"line": line, "sell_price": _dec(args.get("selling_price")),
                                          "supplier": args.get("supplier")})
    if name == "record_customer_payment":
        return Intent("record_payment", {"customer": args.get("customer"), "amount": _dec(args.get("amount")),
                                         "method": args.get("payment_method") or "cash"})
    if name == "create_invoice":
        return Intent("invoice", {"customer": args.get("customer"), "lines": _lines(args.get("lines")),
                                  "due_days": args.get("due_in_days") or 7, "phone": args.get("customer_phone")})
    simple = {
        "get_report": ("report", {"period": args.get("period", "today")}),
        "get_stock": ("stock", {"item": args.get("item"), "low_only": bool(args.get("low_only"))}),
        "get_debtors": ("debtors", {}),
        "draft_reminders": ("remind", {"customer": args.get("customer")}),
        "set_item_price": ("set_price", {"item": args.get("item"), "price": _dec(args.get("price"))}),
        "count_stock": ("stock_count", {"item": args.get("item"), "quantity": args.get("quantity")}),
        "set_reorder_level": ("reorder_level", {"item": args.get("item"), "level": args.get("level")}),
        "save_customer_phone": ("set_customer_phone", {"customer": args.get("customer"), "phone": args.get("phone")}),
        "get_credit_profile": ("credit_score", {}),
        "get_lesson": ("lesson", {"query": args.get("topic")}),
        "undo_last_entry": ("undo", {}),
        "get_recent_records": ("recent", {}),
    }
    if name in simple:
        intent_name, intent_args = simple[name]
        return Intent(intent_name, intent_args)
    raise KeyError(name)


@dataclass
class AgentResult:
    replies: list[Reply] = field(default_factory=list)
    used_tools: list[str] = field(default_factory=list)
    final_text: str = ""


class ClaudeAgent:
    def __init__(self, model: str, effort: str = "low", client: Any = None):
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.effort = effort

    def _system(self, ctx: Context) -> list[dict[str, Any]]:
        from ..i18n import LANGUAGES
        from ..money import currency_for
        from ..timeutil import local_today

        biz = ctx.business
        details = (
            f"Business: {biz.display_name}" + (f" ({biz.business_type})" if biz.business_type else "") + "\n"
            f"Owner: {biz.owner_name or 'unknown'}\n"
            f"Currency: {currency_for(biz.currency).name} ({biz.currency})\n"
            f"Default language: {LANGUAGES.get(biz.language, 'English')}\n"
            f"Country: {biz.country or 'unknown'}\n"
            f"Today: {local_today(biz.timezone).isoformat()}"
        )
        return [
            {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": details},
        ]

    def _create(self, ctx: Context, messages: list[dict[str, Any]]) -> Any:
        return self.client.beta.messages.create(
            model=self.model,
            max_tokens=4000,
            system=self._system(ctx),
            tools=TOOLS,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )

    def run(self, ctx: Context, text: str, history: list[dict[str, str]]) -> AgentResult:
        messages: list[dict[str, Any]] = _alternate(history + [{"role": "user", "content": text}])
        result = AgentResult()
        for _ in range(MAX_TOOL_ROUNDS):
            response = self._create(ctx, messages)
            if response.stop_reason == "refusal":
                log.warning("model declined request: %s", getattr(response, "stop_details", None))
                break
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            texts = [b.text for b in response.content if b.type == "text" and b.text.strip()]
            if response.stop_reason != "tool_use" or not tool_uses:
                result.final_text = "\n".join(texts).strip()
                break
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in tool_uses:
                tool_results.append(self._run_tool(ctx, block, result))
            messages.append({"role": "user", "content": tool_results})
        if result.final_text and result.final_text.strip().upper().rstrip(".") != "OK":
            result.replies.append(Reply(result.final_text))
        return result

    def _run_tool(self, ctx: Context, block: Any, result: AgentResult) -> dict[str, Any]:
        args = block.input if isinstance(block.input, dict) else json.loads(block.input or "{}")
        try:
            intent = tool_to_intent(block.name, args)
            # Savepoint so a failing tool cannot leave half-written records behind.
            ctx.conn.execute("SAVEPOINT tool")
            try:
                replies = execute(ctx, intent)
                ctx.conn.execute("RELEASE SAVEPOINT tool")
            except Exception:
                ctx.conn.execute("ROLLBACK TO SAVEPOINT tool")
                ctx.conn.execute("RELEASE SAVEPOINT tool")
                raise
        except Exception as exc:  # report the failure to the model instead of crashing the conversation
            log.exception("tool %s failed", block.name)
            return {"type": "tool_result", "tool_use_id": block.id, "content": f"Error: {exc}", "is_error": True}
        result.used_tools.append(block.name)
        result.replies.extend(replies)
        content = "\n\n".join(r.text for r in replies if r.text) or "done"
        return {"type": "tool_result", "tool_use_id": block.id, "content": content}


def _alternate(turns: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Merge consecutive same-role turns and make sure the conversation starts with the user."""
    out: list[dict[str, Any]] = []
    for turn in turns:
        if not turn.get("content"):
            continue
        if out and out[-1]["role"] == turn["role"]:
            out[-1]["content"] += "\n" + turn["content"]
        else:
            out.append({"role": turn["role"], "content": turn["content"]})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out
