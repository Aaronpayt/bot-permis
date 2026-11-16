# Bot qui vérifie les dates d'examen pratique sur candidate.aibv.be
# et envoie un mail urgent si une date avant DATE_LIMITE est dispo.
# Il ne réserve JAMAIS rien : il regarde seulement.

import os
import re
import sys
import smtplib
import datetime
from email.mime.text import MIMEText
from playwright.sync_api import sync_playwright

# --- Config (vient des Secrets / variables GitHub) ---
URL = "https://candidate.aibv.be"
EMAIL = os.environ["AIBV_EMAIL"]
MDP = os.environ["AIBV_MDP"]
GMAIL = os.environ["GMAIL_ADRESSE"]
GMAIL_MDP = os.environ["GMAIL_MDP_APP"]

# Date limite : on cherche une date AVANT celle-ci
DATE_LIMITE = datetime.date.fromisoformat(os.environ.get("DATE_LIMITE") or "2027-01-10")

# Centres à vérifier (texte exact affiché sur le site)
CENTRES = (os.environ.get("CENTRES") or "BRAINE-LE-COMTE,COUILLET").split(",")

# Les deux types d'examen (2026 = dates de décembre, 2027 = dates de janvier)
EXAMENS = ["PRATIQUE B FINAL FL (2026)", "PRATIQUE B FINAL FL (2027)"]

MOIS = {
    "janvier": 1, "février": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "août": 8, "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12,
}
JOURS = "lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche"
# Exemple de ligne : "mardi 19 janvier" ou "jeudi 21 janvier - Aucun slot disponible"
REGEX_DATE = re.compile(rf"^({JOURS})\s+(\d{{1,2}})\s+({'|'.join(MOIS)})(.*)$", re.I)


def cliquer(page, texte):
    # Clique sur l'élément qui a exactement ce texte
    page.get_by_text(texte, exact=True).first.click()
    page.wait_for_timeout(1000)


def suivant(page):
    # Bouton "ÉTAPE SUIVANTE"
    page.get_by_text(re.compile(r"étape suivante", re.I)).first.click()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)


def connexion(page):
    page.goto(URL)
    page.wait_for_load_state("networkidle")
    page.locator("input[type=email], input[name*=mail i], input[type=text]").first.fill(EMAIL)
    page.locator("input[type=password]").first.fill(MDP)
    page.locator("input[type=password]").first.press("Enter")
    # On attend d'être sur le profil (bouton "Prendre rendez-vous")
    page.get_by_text(re.compile(r"prendre rendez-vous", re.I)).first.wait_for(timeout=30000)


def convertir_date(jour, mois_txt):
    # Le site n'affiche pas l'année : on la devine
    aujourdhui = datetime.date.today()
    d = datetime.date(aujourdhui.year, MOIS[mois_txt.lower()], int(jour))
    if d < aujourdhui - datetime.timedelta(days=30):
        d = d.replace(year=aujourdhui.year + 1)
    return d


def lire_dates(page):
    # Attend que la liste des dates s'affiche (max 20 s)
    for _ in range(20):
        lignes = [l.strip() for l in page.inner_text("body").splitlines()]
        trouvees = [REGEX_DATE.match(l) for l in lignes if REGEX_DATE.match(l)]
        if trouvees:
            break
        page.wait_for_timeout(1000)
    else:
        raise Exception("Page des dates introuvable")

    dispo = []
    for m in trouvees:
        if "aucun slot" in m.group(4).lower():
            continue  # jour complet
        dispo.append(convertir_date(m.group(2), m.group(3)))
    return dispo


def verifier(page, centre, examen):
    page.goto(URL)
    page.wait_for_load_state("networkidle")
    page.get_by_text(re.compile(r"prendre rendez-vous", re.I)).last.click()
    page.wait_for_timeout(1500)

    cliquer(page, "NON")              # étape 1 : déchéance
    suivant(page)
    cliquer(page, centre)             # étape 2 : centre
    suivant(page)
    cliquer(page, "PERMIS PRATIQUE")  # étape 3 : type
    suivant(page)
    cliquer(page, examen)             # étape 4 : examen
    suivant(page)
    return lire_dates(page)           # étape 5 : dates


def envoyer_mail(resultats):
    resultats.sort(key=lambda r: r[2])
    premiere = resultats[0][2].strftime("%d/%m/%Y")
    jours_fr = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    lignes = [f"- {c} : {jours_fr[d.weekday()]} {d.strftime('%d/%m/%Y')}" for c, ex, d in resultats]
    corps = (
        "Dates dispo avant le " + DATE_LIMITE.strftime("%d/%m/%Y") + " :\n\n"
        + "\n".join(lignes)
        + "\n\nRéserve vite : " + URL
    )
    msg = MIMEText(corps, "plain", "utf-8")
    msg["Subject"] = f"URGENT - Date permis dispo le {premiere}"
    msg["From"] = GMAIL
    msg["To"] = GMAIL
    msg["X-Priority"] = "1"
    msg["Importance"] = "High"
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL, GMAIL_MDP)
        s.send_message(msg)


def main():
    resultats = []  # (centre, examen, date)
    erreurs = 0
    total = 0

    with sync_playwright() as p:
        navigateur = p.chromium.launch()
        page = navigateur.new_page(locale="fr-BE")
        connexion(page)

        for centre in CENTRES:
            for examen in EXAMENS:
                total += 1
                try:
                    dates = verifier(page, centre.strip(), examen)
                    bonnes = [d for d in dates if d < DATE_LIMITE]
                    print(f"{centre} / {examen} : {len(dates)} jours dispo, {len(bonnes)} avant la limite")
                    resultats += [(centre, examen, d) for d in bonnes]
                except Exception as e:
                    erreurs += 1
                    print(f"ERREUR {centre} / {examen} : {e}")
                    page.screenshot(path=f"erreur_{total}.png", full_page=True)

        navigateur.close()

    if resultats:
        envoyer_mail(resultats)
        print("Mail envoyé !")

    # Si tout a planté, on fait échouer le run (GitHub t'enverra un mail d'erreur)
    if erreurs == total:
        sys.exit(1)


if __name__ == "__main__":
    main()
