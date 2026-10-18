"""User-facing strings in English, Swahili, French and Nigerian Pidgin.

Keep messages short: they are read on small screens, often on slow connections.
Placeholders use str.format names. Missing translations fall back to English.
"""

from __future__ import annotations

LANGUAGES = {"en": "English", "sw": "Kiswahili", "fr": "Français", "pcm": "Pidgin"}

STRINGS: dict[str, dict[str, str]] = {
    "welcome": {
        "en": "👋 Welcome to *AfriBiz*, your business assistant on WhatsApp.\nI keep your records, track stock, "
              "make invoices, chase customers who owe you and build your credit profile.\n\nWhat is the *name of your business*?",
        "sw": "👋 Karibu *AfriBiz*, msaidizi wako wa biashara kwenye WhatsApp.\nNinaweka rekodi zako, nafuatilia mzigo, "
              "natengeneza ankara, nakumbusha wadeni na kujenga wasifu wako wa mkopo.\n\nBiashara yako *inaitwa nani*?",
        "fr": "👋 Bienvenue sur *AfriBiz*, votre assistant d'affaires sur WhatsApp.\nJe tiens vos comptes, suis votre stock, "
              "fais vos factures, relance vos clients et construis votre profil de crédit.\n\nQuel est le *nom de votre commerce* ?",
        "pcm": "👋 Welcome to *AfriBiz*, your business assistant for WhatsApp.\nI go keep your records, track your goods, "
               "write invoice, remind people wey owe you and build your credit profile.\n\nWetin be the *name of your business*?",
    },
    "ask_type": {
        "en": "Nice to meet you, *{name}*! What do you sell or do? (e.g. groceries, tailoring, salon, phone repairs)",
        "sw": "Nimefurahi kukujua, *{name}*! Unauza nini au unafanya nini? (mf. vyakula, ushonaji, saluni)",
        "fr": "Enchanté, *{name}* ! Que vendez-vous ou que faites-vous ? (ex. alimentation, couture, salon)",
        "pcm": "I happy to meet you, *{name}*! Wetin you dey sell or do? (e.g. provisions, tailoring, salon)",
    },
    "onboarded": {
        "en": "✅ All set! Your currency is *{currency}*. Just talk to me like a person, by text or voice note:\n\n"
              "• _sold 3 bread @ 60_\n• _sold 2 sugar to Mary on credit_\n• _spent 200 transport_\n"
              "• _bought 20 soda for 800_\n• _Mary paid 300_\n• _report_  • _stock_  • _debtors_  • _score_\n\n"
              "Send *help* any time. Here is your first 1-minute lesson 👇",
        "sw": "✅ Tayari! Sarafu yako ni *{currency}*. Niongeleshe kama mtu, kwa maandishi au sauti:\n\n"
              "• _nimeuza mkate 3 @ 60_\n• _nimeuza sukari 2 kwa Mary kwa deni_\n• _nimetumia 200 nauli_\n"
              "• _nimenunua soda 20 kwa 800_\n• _Mary amelipa 300_\n• _report_  • _stock_  • _debtors_  • _score_\n\n"
              "Tuma *help* wakati wowote. Hili ni somo lako la kwanza 👇",
        "fr": "✅ C'est prêt ! Votre monnaie est *{currency}*. Parlez-moi simplement, par écrit ou par note vocale :\n\n"
              "• _vendu 3 pains à 60_\n• _vendu 2 sucres à Marie à crédit_\n• _dépensé 200 transport_\n"
              "• _acheté 20 sodas pour 800_\n• _Marie a payé 300_\n• _report_  • _stock_  • _debtors_  • _score_\n\n"
              "Envoyez *help* à tout moment. Voici votre première leçon 👇",
        "pcm": "✅ Everything don set! Your money na *{currency}*. Just yarn me normal, text or voice note:\n\n"
               "• _I sell 3 bread 60 each_\n• _I sell 2 sugar to Mary for credit_\n• _I spend 200 transport_\n"
               "• _I buy 20 minerals for 800_\n• _Mary don pay 300_\n• _report_  • _stock_  • _debtors_  • _score_\n\n"
               "Send *help* anytime. See your first lesson 👇",
    },
    "help": {
        "en": "*What I can do* 🧾\n"
              "*Sales:* sold 2 rice @ 150 · sold soap 50 to Juma on credit\n"
              "*Expenses:* spent 300 transport · rent 5000 · took 500 for home\n"
              "*Stock:* bought 10 rice for 1200 · stock · count sugar 12 · reorder rice at 5 · price rice 160\n"
              "*Customers:* debtors · Juma paid 200 · remind · remind Juma · phone Juma 0712345678\n"
              "*Invoices:* invoice Juma 2 rice @ 150\n"
              "*Reports:* report · report week · report month · recent · dashboard\n"
              "*Mobile money:* forward M-PESA/MoMo/Airtel SMS · score · share score\n"
              "*Learn:* lesson · lesson 5 · lesson pricing · progress\n"
              "*Other:* undo · language sw/fr/pcm/en · currency KES · delete my data",
        "sw": "*Ninachoweza kufanya* 🧾\n"
              "*Mauzo:* nimeuza mchele 2 @ 150 · nimeuza sabuni 50 kwa Juma kwa deni\n"
              "*Gharama:* nimetumia 300 nauli · kodi 5000 · nimechukua 500 nyumbani\n"
              "*Mzigo:* nimenunua mchele 10 kwa 1200 · stock · count sukari 12 · reorder mchele at 5\n"
              "*Wateja:* debtors · Juma amelipa 200 · remind · remind Juma\n"
              "*Ankara:* invoice Juma 2 mchele @ 150\n"
              "*Ripoti:* report · report week · report month · dashboard\n"
              "*Pesa ya simu:* tuma SMS za M-PESA · score\n"
              "*Jifunze:* lesson · lesson 5 · progress\n"
              "*Nyingine:* undo · language en · delete my data",
        "fr": "*Ce que je sais faire* 🧾\n"
              "*Ventes :* vendu 2 riz à 150 · vendu savon 50 à Moussa à crédit\n"
              "*Dépenses :* dépensé 300 transport · loyer 5000 · pris 500 pour la maison\n"
              "*Stock :* acheté 10 riz pour 1200 · stock · count sucre 12 · reorder riz at 5\n"
              "*Clients :* debtors · Moussa a payé 200 · remind\n"
              "*Factures :* invoice Moussa 2 riz à 150\n"
              "*Rapports :* report · report week · report month · dashboard\n"
              "*Mobile money :* transférez vos SMS Orange Money/MTN/Wave · score\n"
              "*Apprendre :* lesson · progress\n"
              "*Autre :* undo · language en · delete my data",
        "pcm": "*Wetin I fit do* 🧾\n"
               "*Sales:* I sell 2 rice 150 each · I sell soap 50 to Emeka for credit\n"
               "*Expenses:* I spend 300 transport · shop rent 5000 · I take 500 for house\n"
               "*Goods:* I buy 10 rice for 1200 · stock · count sugar 12 · reorder rice at 5\n"
               "*Customers:* debtors · Emeka don pay 200 · remind\n"
               "*Invoice:* invoice Emeka 2 rice @ 150\n"
               "*Reports:* report · report week · report month · dashboard\n"
               "*Bank/MoMo alerts:* forward your alert · score\n"
               "*Learn:* lesson · progress\n"
               "*Others:* undo · language en · delete my data",
    },
    "not_understood": {
        "en": "Sorry, I didn't get that 🤔 Try _sold 2 bread @ 50_, _spent 100 transport_ or send *help*.",
        "sw": "Samahani, sijaelewa 🤔 Jaribu _nimeuza mkate 2 @ 50_, _nimetumia 100 nauli_ au tuma *help*.",
        "fr": "Désolé, je n'ai pas compris 🤔 Essayez _vendu 2 pains à 50_, _dépensé 100 transport_ ou *help*.",
        "pcm": "Sorry, I no understand 🤔 Try _I sell 2 bread 50_, _I spend 100 transport_ or send *help*.",
    },
    "voice_unavailable": {
        "en": "🎙️ I received your voice note, but voice is not switched on yet. Please type it for now.",
        "sw": "🎙️ Nimepokea ujumbe wa sauti, lakini huduma ya sauti bado haijawashwa. Tafadhali andika kwa sasa.",
        "fr": "🎙️ J'ai reçu votre note vocale, mais la voix n'est pas encore activée. Écrivez-la pour l'instant.",
        "pcm": "🎙️ I don receive your voice note, but voice never on. Abeg type am for now.",
    },
    "heard": {"en": "🎙️ I heard: “{text}”", "sw": "🎙️ Nimesikia: “{text}”", "fr": "🎙️ J'ai entendu : « {text} »",
              "pcm": "🎙️ I hear: “{text}”"},
    "undo_none": {"en": "There is nothing to undo.", "sw": "Hakuna cha kufuta.", "fr": "Rien à annuler.",
                  "pcm": "Nothing dey to undo."},
    "deleted": {
        "en": "🗑️ All your data has been deleted. Send any message to start again.",
        "sw": "🗑️ Data yako yote imefutwa. Tuma ujumbe wowote kuanza upya.",
        "fr": "🗑️ Toutes vos données ont été supprimées. Envoyez un message pour recommencer.",
        "pcm": "🗑️ We don delete all your data. Send any message to start again.",
    },
    "confirm_delete": {
        "en": "⚠️ This will permanently delete ALL your records. Reply *YES DELETE* to confirm.",
        "sw": "⚠️ Hii itafuta rekodi ZAKO ZOTE milele. Jibu *YES DELETE* kuthibitisha.",
        "fr": "⚠️ Cela supprimera définitivement TOUTES vos données. Répondez *YES DELETE* pour confirmer.",
        "pcm": "⚠️ This one go delete ALL your records forever. Reply *YES DELETE* to confirm.",
    },
    "language_set": {"en": "Language set to English ✅", "sw": "Lugha imewekwa: Kiswahili ✅",
                     "fr": "Langue : Français ✅", "pcm": "Language don set to Pidgin ✅"},
    "no_debtors": {"en": "🎉 Nobody owes you money right now.", "sw": "🎉 Hakuna anayekudai sasa hivi.",
                   "fr": "🎉 Personne ne vous doit d'argent.", "pcm": "🎉 Nobody owe you money now."},
    "no_stock": {"en": "No items yet. Add stock like: _bought 20 soap for 1000_",
                 "sw": "Bado hakuna bidhaa. Ongeza hivi: _nimenunua sabuni 20 kwa 1000_",
                 "fr": "Aucun article. Ajoutez : _acheté 20 savons pour 1000_",
                 "pcm": "No goods yet. Add like this: _I buy 20 soap for 1000_"},
    "lessons_done": {"en": "🎓 You have finished all lessons! Send *lesson 1* to review any of them.",
                     "sw": "🎓 Umemaliza masomo yote! Tuma *lesson 1* kurudia.",
                     "fr": "🎓 Vous avez terminé toutes les leçons ! Envoyez *lesson 1* pour réviser.",
                     "pcm": "🎓 You don finish all lessons! Send *lesson 1* to read am again."},
    "quiz_right": {"en": "✅ Correct! {why}", "sw": "✅ Sahihi! {why}", "fr": "✅ Bravo ! {why}",
                   "pcm": "✅ Correct! {why}"},
    "quiz_wrong": {"en": "Not quite. The answer is *{answer}*. {why}", "sw": "Si sahihi. Jibu ni *{answer}*. {why}",
                   "fr": "Pas tout à fait. La réponse est *{answer}*. {why}",
                   "pcm": "E no correct. The answer na *{answer}*. {why}"},
}


def t(key: str, lang: str | None = "en", **kwargs: object) -> str:
    entry = STRINGS.get(key, {})
    text = entry.get(lang or "en") or entry.get("en") or key
    return text.format(**kwargs) if kwargs else text
