"""Test the CRM analyser against what the medical-insurance call centre asked for.

The call centre's spreadsheet ("Communiq AI TOP6") is the specification of the `insurance_ka`
preset: the six most frequent requests (sheet "TOP 6"), the six groups of "information given"
(sheet "TOP 6 გაცემული") and nineteen sub-scenarios with the signals an AI should pick up and
the text it should write (sheet "სცენარები"). This script checks both halves of the promise:

  coverage  — no model, no key. Every row of the spreadsheet is traced to the form: the request,
              its category / sub-category / reaction, the provider rule, the sub-scenario under
              the right request with its signal words, and the template's placeholders. Prints
              the trace and exits non-zero on a gap.

  run       — the real model. Each scenario below is a short Georgian call written so that a
              careful human reviewer would fill the form exactly one way. The form is filled
              exactly as `POST /recordings/{id}/crm` fills it (`crm_extract.fill`, same prompts,
              same passes) and every expected field is compared. Writes <out>.json (every raw
              result) and <out>.md (the scorecard).

    # where the deployment's model and key are (the server, inside the api image):
    docker compose -p cqv3 run --rm --no-deps -T -v "$PWD/backend:/app:z" -v "$PWD/crm_eval:/out:z" api \\
        python scripts/crm_scenarios.py run --from-settings --out /out/run1
    # or anywhere, with a key in the environment:
    ANTHROPIC_API_KEY=... python scripts/crm_scenarios.py run --model claude-opus-4-8 --out crm_run1
    python scripts/crm_scenarios.py coverage path/to/Communiq_AI_TOP6.xlsx

Cost: ~2 model calls per scenario (the preset fills in two passes), 23 scenarios — on Opus a
few dollars a run. Nothing is written to any tenant's data; with --from-settings each call is
metered in `llm_usage` with no workspace, feature `crm_fill`.

The scenarios use invented clinics, people and numbers. The preset's teams are role labels
("medical department"), as in the shipped form.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

CALL_DATE = date(2026, 11, 9)   # a Monday; scenarios speak of "tomorrow", "Thursday" etc.

# --------------------------------------------------------------------------- #
# Scenarios. `expect` values: a string/number/bool must match exactly; a list means "any of";
# {"contains": [...]} means the text value must contain every piece (case-insensitive).
# `texts`: the rendered requested_info / issued_info must contain these facts.
# --------------------------------------------------------------------------- #
O, D = "ოპერატორი", "დაზღვეული"


def _t(*lines: tuple[str, str]) -> str:
    return "\n".join(f"{who}: {text}" for who, text in lines)


SCENARIOS: list[dict] = [
    # ---- გეგმიური ამბულატორია -------------------------------------------------
    {"id": "po_informed", "excel": "სცენარები #1 — ეცნო ქმედება / დდ სქემა / დაფარვის %",
     "transcript": _t(
         (O, "გამარჯობა, სადაზღვევო კომპანია, ნინო გისმენთ."),
         (D, "გამარჯობა. მაინტერესებს, მაგნიტურ-რეზონანსულ ტომოგრაფიას რამდენით მიფარავს პოლისი?"),
         (O, "ერთი წუთით, გადავამოწმებ თქვენს პოლისს... პროვაიდერ კლინიკაში ფინანსდება 70%-ით, "
             "არაპროვაიდერ კლინიკაში კი ანაზღაურების წესით, ხარჯის 50%."),
         (D, "გასაგებია, დიდი მადლობა."),
         (O, "არაფრის, ჯანმრთელობას გისურვებთ.")),
     "expect": {"request": "planned_outpatient", "issued_group": "informed",
                "issued_scenario": "po_informed", "reaction": "first_call",
                "provider": "not_needed", "coverage_percent": 70,
                "category": "medical", "subcategory": "informational"},
     "texts": {"issued_info": ["70"]}},

    {"id": "po_referral", "excel": "სცენარები #2 — მიმართვა გამოიწერა",
     "transcript": _t(
         (O, "გამარჯობა, გისმენთ."),
         (D, "გამარჯობა, კარდიოლოგთან კონსულტაციაზე მინდა წასვლა კლინიკა „მზიურში“ და მიმართვა მჭირდება."),
         (O, "კარგი, გიწერთ მიმართვას კლინიკა „მზიურში“ კარდიოლოგის კონსულტაციაზე. მიმართვის ნომერია 48213. "
             "კონსულტაცია ფინანსდება 100%-ით, მიმართვა აქტიურია 30 დღე."),
         (D, "მადლობა.")),
     "expect": {"request": "planned_outpatient", "issued_scenario": "po_referral",
                "provider": "other", "clinic": {"contains": ["მზიურ"]},
                "reaction": "first_call", "issued_group": ["informed", "yes_no"]},
     "texts": {"issued_info": ["48213"]}},

    {"id": "po_tech", "excel": "სცენარები #3 — გადაეცა ტექნიკურს / კოლეგას",
     "transcript": _t(
         (O, "გამარჯობა, გისმენთ."),
         (D, "გამარჯობა, ექიმმა მითხრა, რომ მიმართვა გამომიწერა ექოსკოპიაზე, მაგრამ აპლიკაციაში არ ჩანს."),
         (O, "ვხედავ, სისტემაში მართლაც არ ფიქსირდება. ეს ტექნიკური ხარვეზია — ახლავე გადავუგზავნი "
             "ტექნიკურ ჯგუფს ელ-ფოსტით და შედეგს გაცნობებთ."),
         (D, "კარგი, ველოდები.")),
     "expect": {"request": "planned_outpatient", "issued_group": "tech_team",
                "issued_scenario": "po_tech", "forwarded_to": "tech", "reaction": "first_call"}},

    {"id": "po_yes_no", "excel": "სცენარები #4 — კი / არა პასუხი (დაფარვაზე)",
     "transcript": _t(
         (O, "გამარჯობა, გისმენთ."),
         (D, "მოკლედ მკითხავ — ფიზიოთერაპიას ფარავს ჩემი პოლისი თუ არა?"),
         (O, "სამწუხაროდ, არა. ფიზიოთერაპია თქვენი პოლისის პირობებით არ ფინანსდება."),
         (D, "გასაგებია, მადლობა.")),
     "expect": {"request": "planned_outpatient", "issued_group": "yes_no",
                "issued_scenario": "po_yes_no", "provider": "not_needed"}},

    {"id": "po_providers", "excel": "სცენარები #5 — პროვაიდერის მითითება",
     "transcript": _t(
         (O, "გამარჯობა, გისმენთ."),
         (D, "სისხლის საერთო ანალიზი მინდა გავიკეთო. სად შეიძლება, რომელ კლინიკაში?"),
         (O, "თქვენი პოლისით შეგიძლიათ მიმართოთ ჩვენს პროვაიდერ კლინიკებს: „მზიური“ ვაკეში და "
             "„ლაბ-ცენტრი“ საბურთალოზე."),
         (D, "მადლობა, „მზიურში“ წავალ.")),
     "expect": {"request": ["planned_outpatient", "provider_clinics"],
                "issued_scenario": "po_providers", "issued_group": "informed"},
     "texts": {"issued_info": ["მზიურ", "ლაბ"]}},

    {"id": "po_booked", "excel": "სცენარები #6 — ჩაწერა ვიზიტზე",
     "transcript": _t(
         (O, "გამარჯობა, გისმენთ."),
         (D, "დერმატოლოგთან მინდა ჩაწერა."),
         (O, "ჩაგწერეთ დერმატოლოგ თამარ ბერიძესთან, 12 ნოემბერს, 15:00 საათზე, კლინიკა „მზიურში“."),
         (D, "მშვენიერია, მადლობა.")),
     "expect": {"request": "planned_outpatient", "issued_group": "booked",
                "issued_scenario": "po_booked", "appointment_at": {"startswith": "2026-11-12T15:00"},
                "provider": "other"},
     "texts": {"issued_info": ["15:00"]}},

    # ---- ქრ. მედიკამენტების გამოწერა ---------------------------------------
    {"id": "cm_tech", "excel": "სცენარები #7 — გადაეცა ტექნიკურს (მეილით); TOP 6 #2 პროვაიდერის წესი",
     "transcript": _t(
         (O, "გამარჯობა, გისმენთ."),
         (D, "ქრონიკული მედიკამენტების გამოწერა მინდა: მეტფორმინი და ლიზინოპრილი. ოჯახის ექიმი "
             "მყავს პარტნიორ კლინიკაში, დავით კაპანაძე."),
         (O, "კარგი, თქვენს მოთხოვნას გადავუგზავნი ტექნიკურ ჯგუფს ელ-ფოსტით, მედიკამენტები "
             "გამოგეწერებათ და შეტყობინებას მიიღებთ."),
         (D, "მადლობა.")),
     "expect": {"request": "chronic_meds", "issued_group": "tech_team", "forwarded_to": "tech",
                "issued_scenario": "cm_tech", "provider": "partner_clinic"},
     "texts": {"requested_info": ["მეტფორმინ"]}},

    {"id": "cm_already", "excel": "სცენარები #8 — უკვე გამოწერილია",
     "transcript": _t(
         (O, "გამარჯობა, გისმენთ."),
         (D, "ქრონიკულ მედიკამენტებზე ვრეკავ, თიროქსინი უნდა გამოვიწერო."),
         (O, "ვამოწმებ... თიროქსინი სისტემაში უკვე აქტიურია, გამოწერილია. შეგიძლიათ აფთიაქში შეიძინოთ."),
         (D, "ოჰ, არ ვიცოდი, მადლობა.")),
     "expect": {"request": "chronic_meds", "issued_scenario": "cm_already",
                "issued_group": "informed", "provider": "not_needed"}},

    # ---- სატელეფონო კონსულტაცია (R1 / R2) -----------------------------------
    {"id": "pc_booked", "excel": "სცენარები #9 — ჩაწერა / დაიგეგმა ვიზიტი (R1)",
     "transcript": _t(
         (O, "გამარჯობა, გისმენთ."),
         (D, "სატელეფონო კონსულტაცია მინდა პედიატრთან, ბავშვს სიცხე აქვს."),
         (O, "ჩაგწერეთ სატელეფონო კონსულტაციაზე პედიატრ ნანა გელაშვილთან, 10 ნოემბერს, 11:30-ზე. "
             "ექიმი თავად დაგირეკავთ."),
         (D, "მადლობა დიდი.")),
     "expect": {"request": "phone_consult", "issued_group": "booked", "issued_scenario": "pc_booked",
                "reaction": "first_call", "appointment_at": {"startswith": "2026-11-10T11:30"}}},

    {"id": "pc_monitoring", "excel": "სცენარები #10 — მონიტორინგზე გადაცემა (R2); TOP 6 #3 რეაგირება",
     "transcript": _t(
         (O, "გამარჯობა, გისმენთ."),
         (D, "ენდოკრინოლოგთან სატელეფონო კონსულტაციაზე მინდა ჩაწერა."),
         (O, "სამწუხაროდ, ამ კვირაში ენდოკრინოლოგთან თავისუფალი დრო ჯერ არ არის. თქვენს მომართვას "
             "გადავცემ მონიტორინგზე და ხვალ დაგიკავშირდებით კონსულტაციის დასაგეგმად."),
         (D, "კარგი, დაველოდები.")),
     "expect": {"request": "phone_consult", "issued_scenario": "pc_monitoring",
                "reaction": "first_call_monitoring"}},

    # ---- მედიკამენტები: ქმედება / ლიმიტი / % --------------------------------
    {"id": "md_tech", "excel": "სცენარები #11 — გადაეცა ტექნიკურს / კოლეგას",
     "transcript": _t(
         (O, "გამარჯობა, გისმენთ."),
         (D, "აფთიაქში ამოქსიცილინი ვერ შევიძინე, 0% იწერება, თუმცა ექიმმა გამომიწერა."),
         (O, "გასაგებია. ამ საკითხს გადავუგზავნი ტექნიკურ ჯგუფს ელ-ფოსტით, გადაამოწმებენ და დაგიკავშირდებიან."),
         (D, "კარგი.")),
     "expect": {"request": "medicines", "issued_group": "tech_team", "issued_scenario": "md_tech",
                "forwarded_to": "tech"},
     "texts": {"issued_info": ["ამოქსიცილინ"]}},

    {"id": "md_informed", "excel": "სცენარები #12 — ეცნო ქმედება / ლიმიტი / %",
     "transcript": _t(
         (O, "გამარჯობა, გისმენთ."),
         (D, "მაინტერესებს, ნუროფენს რამდენი პროცენტით მიფინანსებს პოლისი და რა ლიმიტი მაქვს?"),
         (O, "მედიკამენტები თქვენს პოლისში ფინანსდება 50%-ით, წლიური ლიმიტია 500 ლარი, "
             "აქედან ჯერ 120 ლარი გაქვთ გახარჯული."),
         (D, "მადლობა.")),
     "expect": {"request": "medicines", "issued_group": "informed", "issued_scenario": "md_informed",
                "coverage_percent": 50},
     "texts": {"issued_info": ["50", "500"]}},

    {"id": "md_help", "excel": "სცენარები #13 — დახმარება მიმართვის მოთხოვნაში",
     "transcript": _t(
         (O, "გამარჯობა, გისმენთ."),
         (D, "მედიკამენტზე მიმართვა როგორ მოვითხოვო? აპლიკაციაში ვერ ვაკეთებ."),
         (O, "აპლიკაციაში შედით „ჩემი პოლისი“ → „მიმართვები“ → „ახალი მოთხოვნა“, აირჩიეთ ოჯახის ექიმი "
             "და დაადასტურეთ. ინსტრუქციას SMS-ითაც გამოგიგზავნით."),
         (D, "აჰ, ახლა გავიგე, მადლობა.")),
     "expect": {"request": "medicines", "issued_scenario": "md_help", "issued_group": "informed"}},

    # ---- საგარანტიო წერილი ---------------------------------------------------
    {"id": "gl_forwarded", "excel": "სცენარები #14 — გადაეცა მენეჯერს / სამედიცინო დეპარტამენტს",
     "transcript": _t(
         (O, "გამარჯობა, გისმენთ."),
         (D, "საგარანტიო წერილზე ვრეკავ, განაცხადის ნომერია 20391, ოპერაცია ზეგ მაქვს კლინიკა „მზიურში“ "
             "და სასწრაფოდ მჭირდება, შეიძლება დაჩქარება?"),
         (O, "გასაგებია. დაჩქარების მოთხოვნას ახლავე გადავუგზავნი სამედიცინო დეპარტამენტს ელ-ფოსტით."),
         (D, "მადლობა.")),
     "expect": {"request": "guarantee_letter", "issued_group": "forwarded", "forwarded_to": "medical",
                "issued_scenario": "gl_forwarded", "application_number": {"contains": ["20391"]},
                "provider": "other", "clinic": {"contains": ["მზიურ"]}},
     "texts": {"issued_info": ["20391"]}},

    {"id": "gl_status", "excel": "სცენარები #15 — ეცნო სტატუსი / ვადები",
     "transcript": _t(
         (O, "გამარჯობა, გისმენთ."),
         (D, "საგარანტიო წერილი რა ეტაპზეა? განაცხადი 30477."),
         (O, "თქვენი საგარანტიო განხილვაშია, განხილვის ვადაა 3 სამუშაო დღე. დამატებითი დოკუმენტი არ არის მოთხოვნილი."),
         (D, "კარგი, მადლობა.")),
     "expect": {"request": "guarantee_letter", "issued_group": "informed", "issued_scenario": "gl_status",
                "application_number": {"contains": ["30477"]}},
     "texts": {"issued_info": ["3"]}},

    {"id": "gl_action", "excel": "სცენარები #16 — ეცნო ქმედება (წარმოადგინოს / გადაიხადოს)",
     "transcript": _t(
         (O, "გამარჯობა, გისმენთ."),
         (D, "ოპერაციისთვის საგარანტიო წერილი როგორ მივიღო? არ მომითხოვია ჯერ."),
         (O, "საგარანტიო წერილი კლინიკამ უნდა მოითხოვოს ჩვენგან. თუ კლინიკა ვერ ითხოვს, შეგიძლიათ თავად "
             "გადაიხადოთ და შემდეგ დოკუმენტაცია წარმოადგინოთ ანაზღაურებაზე."),
         (D, "გასაგებია.")),
     "expect": {"request": "guarantee_letter", "issued_group": "informed", "issued_scenario": "gl_action"}},

    # ---- ონლაინ ანაზღაურება ---------------------------------------------------
    {"id": "or_forwarded", "excel": "სცენარები #17 — გადაეცა მენეჯერს (მეილით)",
     "transcript": _t(
         (O, "გამარჯობა, გისმენთ."),
         (D, "ონლაინ ანაზღაურებაზე ვრეკავ, განაცხადი MD-55812. უარი მომივიდა და მიზეზი ვერ გავიგე, "
             "მენეჯერთან მინდა გასაუბრება."),
         (O, "გასაგებია, თქვენს მოთხოვნას გადავუგზავნი მენეჯერს, გიორგი ლომიძეს, ელ-ფოსტით და დაგიკავშირდებიან."),
         (D, "კარგი.")),
     "expect": {"request": "online_reimbursement", "issued_group": "forwarded", "forwarded_to": "manager",
                "issued_scenario": "or_forwarded", "application_number": {"contains": ["55812"]}},
     "texts": {"issued_info": ["55812"]}},

    {"id": "or_rejected", "excel": "სცენარები #18 — ეცნო დაწუნების / შეჩერების მიზეზი",
     "transcript": _t(
         (O, "გამარჯობა, გისმენთ."),
         (D, "რატომ არ დამიფინანსდა ანაზღაურება? განაცხადი EMD-66190."),
         (O, "ვამოწმებ... განაცხადი შეჩერებულია, რადგან ფორმა 100 არ არის ატვირთული. ახალი განაცხადი "
             "საჭირო არ არის — ფორმა 100 იმავე განაცხადში დაამატეთ."),
         (D, "კარგი, ავტვირთავ.")),
     "expect": {"request": "online_reimbursement", "issued_group": "informed", "issued_scenario": "or_rejected",
                "application_number": {"contains": ["66190"]}},
     "texts": {"issued_info": ["100"]}},

    {"id": "or_status", "excel": "სცენარები #19 — ეცნო სტატუსი / ვადები",
     "transcript": _t(
         (O, "გამარჯობა, გისმენთ."),
         (D, "ანაზღაურებაზე დოკუმენტები ავტვირთე, EMD-77120, პასუხს ველოდები. ჩაირიცხა?"),
         (O, "განაცხადი განხილვაშია, განხილვის ვადაა 5 სამუშაო დღე. შედეგს SMS-ით მიიღებთ."),
         (D, "მადლობა.")),
     "expect": {"request": "online_reimbursement", "issued_group": "informed", "issued_scenario": "or_status",
                "application_number": {"contains": ["77120"]}}},

    # ---- "TOP 6 გაცემული" groups and rules not covered above ----------------
    {"id": "clinic_confirmed", "excel": "TOP 6 გაცემული #6 — დადასტურდა % / ფრანშიზა (კლინიკისთვის)",
     "transcript": _t(
         (O, "გამარჯობა, გისმენთ."),
         (D, "გამარჯობა, კლინიკა „მზიურიდან“ გირეკავთ, რეგისტრატურა. თქვენი დაზღვეული გიორგი ნაკაშიძე "
             "გადაუდებელ ამბულატორიაზეა, რამდენი პროცენტით ფინანსდება?"),
         (O, "ვადასტურებ: გადაუდებელი ამბულატორია ფინანსდება 90%-ით, ფრანშიზის გარეშე."),
         (D, "მადლობა.")),
     "expect": {"issued_group": "clinic_confirmed", "coverage_percent": 90, "provider": "other",
                "clinic": {"contains": ["მზიურ"]}}},

    {"id": "policy_validity", "excel": "TOP 6 გაცემული #4 — კი / არა პასუხი (პოლისის ვალიდურობა)",
     "transcript": _t(
         (O, "გამარჯობა, გისმენთ."),
         (D, "ჩემი პოლისი ჯერ კიდევ აქტიურია?"),
         (O, "დიახ, თქვენი პოლისი აქტიურია 2027 წლის 31 მარტამდე."),
         (D, "მადლობა.")),
     "expect": {"request": "policy_validity", "issued_group": "yes_no", "provider": "not_needed"}},

    {"id": "state_insurance", "excel": "TOP 6 — ეცნო სახ. დაზღვევაზე",
     "transcript": _t(
         (O, "გამარჯობა, გისმენთ."),
         (D, "კატარაქტის ოპერაციას თუ მიფარავს ჩემი პოლისი?"),
         (O, "თქვენი პოლისით კატარაქტის ოპერაცია არ ფინანსდება, მაგრამ შეგიძლიათ ისარგებლოთ "
             "საყოველთაო ჯანდაცვის სახელმწიფო პროგრამით — ის ამ ოპერაციას ფარავს."),
         (D, "კარგი, გავარკვევ.")),
     "expect": {"state_insurance_informed": True, "issued_group": ["yes_no", "informed"]}},

    {"id": "off_topic", "excel": "„სხვა“ — მოთხოვნა, რომელიც TOP 6-ში არ არის",
     "transcript": _t(
         (O, "გამარჯობა, გისმენთ."),
         (D, "ავტომობილის დაზღვევაზე მინდა ვიკითხო, ავარია მქონდა გუშინ."),
         (O, "ავტოდაზღვევის საკითხებზე გადაგრთავთ ავტოდაზღვევის განყოფილებაზე, ერთი წუთით."),
         (D, "კარგი.")),
     "expect": {"request": "other"}},
]


# --------------------------------------------------------------------------- #
# Comparing a result with the expectation (pure — tested in tests/test_crm_scenarios.py)
# --------------------------------------------------------------------------- #
def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def check_value(expected, actual) -> bool:
    if isinstance(expected, dict):
        if "contains" in expected:
            return all(_norm(p) in _norm(actual) for p in expected["contains"])
        if "startswith" in expected:
            return _norm(actual).startswith(_norm(expected["startswith"]))
        raise ValueError(f"unknown matcher {expected}")
    if isinstance(expected, list):
        return any(check_value(e, actual) for e in expected)
    if isinstance(expected, bool):
        return actual is expected
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            return abs(float(actual) - float(expected)) < 1e-6
        except (TypeError, ValueError):
            return False
    return str(actual) == str(expected)


def grade(scenario: dict, result: dict) -> dict:
    """`{"checks": [{field, expected, actual, ok}], "missing_required": [...], "ok": bool}`."""
    by_key = {f["key"]: f for f in result.get("fields", [])}
    checks = []
    for key, exp in scenario.get("expect", {}).items():
        f = by_key.get(key, {})
        checks.append({"field": key, "expected": exp, "actual": f.get("value"),
                       "display": f.get("display"), "ok": check_value(exp, f.get("value"))})
    for key, pieces in scenario.get("texts", {}).items():
        f = by_key.get(key, {})
        shown = f.get("display") or f.get("value")
        checks.append({"field": f"{key} (text)", "expected": {"contains": pieces},
                       "actual": shown, "display": shown,
                       "ok": check_value({"contains": pieces}, shown)})
    # every scenario: the two free texts are written, in Georgian
    for key in ("requested_info", "issued_info"):
        f = by_key.get(key, {})
        shown = str(f.get("display") or f.get("value") or "")
        geo = sum(1 for ch in shown if "Ⴀ" <= ch <= "ჿ")
        checks.append({"field": f"{key} (Georgian)", "expected": "non-empty Georgian text",
                       "actual": shown, "display": shown, "ok": geo >= 10})
    missing = list(result.get("missing_required") or [])
    return {"checks": checks, "missing_required": missing,
            "ok": all(c["ok"] for c in checks) and not missing}


# --------------------------------------------------------------------------- #
# Coverage: the spreadsheet traced to the preset (no model)
# --------------------------------------------------------------------------- #
# The spreadsheet names the call centre's real teams and partner clinic; the shipped preset
# carries role labels in their place. This is the mapping the trace uses.
REAL_TO_ROLE = {"კურაციო": "partner_clinic", "ურგენტ": "urgent", "რიმოუთ": "remote",
                "რემოუთ": "remote", "მარმედ": "partner", "საფორთ": "support",
                "მედიქალ": "medical", "ტექ": "tech", "მენეჯერ": "manager"}
REQUEST_KEYS = {  # spreadsheet "მოთხოვნა" prefix -> option key
    "გეგმიური ამბულატორია": "planned_outpatient", "ქრ. მედიკამენტების გამოწერა": "chronic_meds",
    "სატელეფონო კონსულტაციაზე ჩაწერა": "phone_consult", "მედიკამენტები - ქმედება": "medicines",
    "საგარანტიო წერილი": "guarantee_letter", "ონლაინ ანაზღაურება": "online_reimbursement",
}
GROUP_KEYS = {"გადაეცა კოლეგას": "forwarded", "ეცნო ინფორმაცია": "informed",
              "გადაეცა ტექნიკურ": "tech_team", "კი / არა": "yes_no", "ჩაწერა / დაიგეგმა": "booked",
              "დადასტურდა %": "clinic_confirmed"}
SLOT_RE = re.compile(r"\[([^\[\]]+)\]")


def _request_key(text: str) -> str | None:
    t = str(text or "").strip()
    return next((k for p, k in REQUEST_KEYS.items() if t.startswith(p)), None)


def coverage(xlsx: Path, defn: dict) -> tuple[list[dict], int]:
    """Rows `{sheet, row, requirement, covered_by, ok, note}` and the number of gaps."""
    from openpyxl import load_workbook

    wb = load_workbook(xlsx, data_only=True)
    by_key = {f["key"]: f for f in defn["fields"]}
    opts = {k: {o["key"]: o for o in (f.get("options") or [])} for k, f in by_key.items()}
    rows: list[dict] = []

    def add(sheet, row, req, by, ok, note=""):
        rows.append({"sheet": sheet, "row": row, "requirement": req, "covered_by": by,
                     "ok": bool(ok), "note": note})

    # --- TOP 6 --------------------------------------------------------------
    ws = wb["TOP 6"]
    for r in list(ws.iter_rows(values_only=True))[1:]:
        if not r or r[0] in (None, ""):
            continue
        n, req, cat, sub, reaction, ins_type, provider, req_tmpl, iss_tmpl = (list(r) + [None] * 9)[:9]
        key = _request_key(req)
        o = opts["request"].get(key) if key else None
        add("TOP 6", n, f"მოთხოვნა: {req}", f"request = {key}", o is not None)
        if o is None:
            continue
        imp = o.get("implies") or {}
        add("TOP 6", n, f"კატეგორია: {cat}", f"implies category={imp.get('category')}",
            imp.get("category") == "medical" and "სამედიცინო" in str(cat))
        add("TOP 6", n, f"ქვე კატეგორია: {sub}", f"implies subcategory={imp.get('subcategory')}",
            imp.get("subcategory") == "informational" and "ინფორმაციული" in str(sub))
        reacts = ["first_call"] + (["first_call_monitoring"] if "R2" in str(reaction) else [])
        add("TOP 6", n, f"რეაგირება: {reaction}", f"reaction ∋ {reacts}",
            all(x in opts["reaction"] for x in reacts))
        add("TOP 6", n, f"დაზღვევის ტიპი: {ins_type}", "insurance_type source=external",
            by_key["insurance_type"]["source"] == "external"
            and {"corporate_health", "individual_health"} <= set(opts["insurance_type"]))
        need = {"other", "not_needed"} | ({"partner_clinic"} if "კურაციო" in str(provider) else set())
        add("TOP 6", n, f"პროვაიდერი: {provider}", f"provider ∋ {sorted(need)}",
            need <= set(opts["provider"]))
        if "კლინიკა დასახელ" in str(provider) or "კონკრეტული" in str(provider):
            add("TOP 6", n, "კლინიკა დასახელებისას — კონკრეტული", "clinic (when provider=other)",
                "clinic" in by_key)
        # requested-info template: every [slot] in the spreadsheet should have a {slot} in the option
        tmpl = o.get("template") or ""
        want = [s for s in SLOT_RE.findall(str(req_tmpl or ""))]
        have = re.findall(r"\{([^{}]+)\}", tmpl)
        add("TOP 6", n, f"მოთხოვნილი ინფორმაცია — შაბლონი ({len(want)} ჩასასმელი)",
            f"request option template ({len(have)} slots: {', '.join(have)})",
            bool(tmpl) and len(have) >= len(want),
            "spreadsheet slots: " + "; ".join(want))
        # issued-info template: the request's sub-scenarios carry the texts
        kids = [x for x in opts["issued_scenario"].values() if x["parent"] == key]
        add("TOP 6", n, "გაცემული ინფორმაცია — შაბლონი", f"{len(kids)} sub-scenario templates under {key}",
            kids and all(x.get("template") for x in kids))

    # --- TOP 6 გაცემული -------------------------------------------------------
    ws = wb["TOP 6 გაცემული"]
    for r in list(ws.iter_rows(values_only=True))[1:]:
        if not r or r[0] in (None, ""):
            continue
        n, group, reqs, reaction, provider, state, how_written, ai_clarify, ai_tmpl = (list(r) + [None] * 9)[:9]
        key = next((k for p, k in GROUP_KEYS.items() if str(group).startswith(p)), None)
        add("TOP 6 გაცემული", n, f"ჯგუფი: {group}", f"issued_group = {key}",
            key in opts["issued_group"])
        if key in ("forwarded", "tech_team"):
            roles = sorted({role for real, role in REAL_TO_ROLE.items() if real in str(how_written)})
            add("TOP 6 გაცემული", n, "ადრესატები („როგორ წერენ ახლა“)", f"forwarded_to ∋ {roles}",
                set(roles) <= set(opts["forwarded_to"]), "real team names → role labels")
        if key == "booked":
            add("TOP 6 გაცემული", n, "ჩაწერის დრო", "appointment_at (datetime, when booked)",
                by_key.get("appointment_at", {}).get("type") == "datetime")
        if key == "clinic_confirmed":
            add("TOP 6 გაცემული", n, "დადასტურებული %", "coverage_percent (percent)",
                by_key.get("coverage_percent", {}).get("type") == "percent")
        add("TOP 6 გაცემული", n, f"ეცნო სახ. დაზღვევაზე: {str(state)[:30]}",
            "state_insurance_informed (yes/no)",
            by_key.get("state_insurance_informed", {}).get("type") == "boolean")
        if ai_clarify:
            add("TOP 6 გაცემული", n, f"AI-მ რა უნდა დააზუსტოს: {str(ai_clarify)[:70]}",
                "issued_info / sub-scenario template slots", "issued_info" in by_key,
                "checked by the model run")

    # --- სცენარები ------------------------------------------------------------
    ws = wb["სცენარები"]
    for i, r in enumerate(list(ws.iter_rows(values_only=True))[1:], 2):
        if not r or not r[1]:
            continue
        req, sub, signal, reaction, tmpl = (list(r) + [None] * 5)[:5]
        key = _request_key(req)
        sub_head = str(sub).split("/")[0].strip()[:14]
        kid = next((x for x in opts["issued_scenario"].values()
                    if x["parent"] == key and _norm(x["label"]).startswith(_norm(sub_head))), None)
        add("სცენარები", i, f"{req} → {sub}", f"issued_scenario = {kid and kid['key']}", kid is not None)
        if kid is None:
            continue
        sig = [s.strip("„“\" ") for s in re.findall(r"„([^“]+)“", str(signal or ""))]
        kw = set(_norm(k) for k in kid.get("keywords") or [])
        missing = [s for s in sig if _norm(s) not in kw]
        add("სცენარები", i, f"სიგნალი: {', '.join(sig) or str(signal)[:60]}",
            f"keywords {sorted(kid.get('keywords') or [])}", not missing,
            ("missing: " + ", ".join(missing)) if missing else
            ("signal is a situation, not words — left to the model" if not sig else ""))
        if "მონიტორინგ" in str(reaction):
            add("სცენარები", i, f"რეაგირება: {reaction}", "reaction = first_call_monitoring",
                "first_call_monitoring" in opts["reaction"])
        want = SLOT_RE.findall(str(tmpl or ""))
        have = re.findall(r"\{([^{}]+)\}", kid.get("template") or "")
        # "[კლინიკა 1], [კლინიკა 2]" is one list, which the form asks for as one slot
        listed = len(want) > 1 and all(re.sub(r"\s*\d+$", "", w) == re.sub(r"\s*\d+$", "", want[0]) for w in want)
        add("სცენარები", i, f"შაბლონი ({len(want)} ჩასასმელი)",
            f"template ({len(have)} slots: {', '.join(have)})",
            bool(kid.get("template")) and (len(have) >= len(want) or (listed and len(have) >= 1)),
            "spreadsheet slots: " + "; ".join(want) + ("  (a list → one slot)" if listed else ""))
    gaps = sum(1 for r in rows if not r["ok"])
    return rows, gaps


def coverage_md(rows: list[dict], gaps: int) -> str:
    L = [f"# CRM form vs. the call centre's spreadsheet — {len(rows)} requirements, {gaps} gaps", ""]
    for sheet in dict.fromkeys(r["sheet"] for r in rows):
        L += [f"## {sheet}", "", "| # | requirement | covered by | ok | note |", "|---|---|---|---|---|"]
        for r in (x for x in rows if x["sheet"] == sheet):
            cell = lambda s: str(s).replace("|", "/").replace("\n", " ")
            L.append(f"| {r['row']} | {cell(r['requirement'])} | {cell(r['covered_by'])} | "
                     f"{'✅' if r['ok'] else '❌'} | {cell(r['note'])} |")
        L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def preset_definition() -> dict:
    from app.services import crm_form, crm_presets

    raw = crm_presets.PRESETS["insurance_ka"]
    defn, errors, _warnings = crm_form.normalize_definition(raw.get("definition", raw))
    if errors:
        raise SystemExit(f"insurance_ka does not validate: {errors}")
    return defn


async def _model_settings(args) -> tuple[str, str]:
    if args.from_settings:
        from app import db
        from app.services import settings_store

        await db.connect()
        cfg = await settings_store.get_effective()
        return cfg.get("anthropic_api_key") or "", args.model or cfg.get("llm_model") or ""
    key = os.environ.get(args.key_env, "")
    if not key or not args.model:
        raise SystemExit(f"set {args.key_env} and --model, or run with --from-settings")
    return key, args.model


async def run(args) -> int:
    from app.services import crm_extract

    defn = preset_definition()
    api_key, model = await _model_settings(args)
    picked = [s for s in SCENARIOS if not args.only or s["id"] in args.only.split(",")]
    results = []
    for s in picked:
        started = time.monotonic()
        try:
            res = await crm_extract.fill(defn, form_key="insurance_ka", form_version=1,
                                         transcript=s["transcript"], segments=None,
                                         api_key=api_key, model=model, client_id=None,
                                         call_date=CALL_DATE)
            g = grade(s, res)
            err = None
        except Exception as exc:  # noqa: BLE001 — one failed call is a failed scenario, not a crash
            res, g, err = None, {"checks": [], "missing_required": [], "ok": False}, str(exc)
        secs = round(time.monotonic() - started, 1)
        results.append({"id": s["id"], "excel": s["excel"], "seconds": secs, "error": err,
                        "grade": g, "result": res})
        bad = [c["field"] for c in g["checks"] if not c["ok"]]
        print(f"{'PASS' if g['ok'] else 'FAIL'} {s['id']:<17} {secs:>5}s"
              + (f"  ✗ {', '.join(bad)}" if bad else "") + (f"  ERROR {err}" if err else ""), flush=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps({"model": model, "results": results},
                                                   ensure_ascii=False, indent=1, default=str),
                                        encoding="utf-8")
    md = scorecard_md(model, results)
    out.with_suffix(".md").write_text(md, encoding="utf-8")
    print("\n" + md.split("\n## ")[0])
    return 0 if all(r["grade"]["ok"] for r in results) else 1


def scorecard_md(model: str, results: list[dict]) -> str:
    passed = sum(1 for r in results if r["grade"]["ok"])
    per_field: dict[str, list[bool]] = {}
    for r in results:
        for c in r["grade"]["checks"]:
            per_field.setdefault(c["field"].split(" (")[0] if "(text)" not in c["field"] else c["field"],
                                 []).append(c["ok"])
    L = [f"# CRM fill — {passed}/{len(results)} scenarios fully right (model `{model}`)", ""]
    L += ["| field | right | of |", "|---|---|---|"]
    for k, oks in sorted(per_field.items(), key=lambda kv: sum(kv[1]) / len(kv[1])):
        L.append(f"| {k} | {sum(oks)} | {len(oks)} |")
    L.append("")
    for r in results:
        L.append(f"## {'✅' if r['grade']['ok'] else '❌'} {r['id']} — {r['excel']} ({r['seconds']} s)")
        if r["error"]:
            L.append(f"\nERROR: {r['error']}\n")
            continue
        L += ["", "| field | expected | got | ok |", "|---|---|---|---|"]
        for c in r["grade"]["checks"]:
            got = str(c.get("display") or c.get("actual") or "—").replace("|", "/").replace("\n", " ")
            L.append(f"| {c['field']} | {json.dumps(c['expected'], ensure_ascii=False)} | {got[:160]} | "
                     f"{'✅' if c['ok'] else '❌'} |")
        if r["grade"]["missing_required"]:
            L.append(f"\nRequired but empty: {', '.join(r['grade']['missing_required'])}")
        L.append("")
    return "\n".join(L)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    cv = sub.add_parser("coverage", help="trace the spreadsheet to the insurance_ka form (no model)")
    cv.add_argument("xlsx")
    cv.add_argument("--out", help="also write the trace as markdown here")
    rn = sub.add_parser("run", help="fill every scenario with the real model and grade it")
    rn.add_argument("--from-settings", action="store_true",
                    help="use the deployment's model and key (needs DATABASE_URL)")
    rn.add_argument("--model", help="model id (default: the deployment's, with --from-settings)")
    rn.add_argument("--key-env", default="ANTHROPIC_API_KEY")
    rn.add_argument("--only", help="comma-separated scenario ids")
    rn.add_argument("--out", default="crm_scenarios_run")
    args = ap.parse_args(argv)
    if args.cmd == "coverage":
        rows, gaps = coverage(Path(args.xlsx), preset_definition())
        md = coverage_md(rows, gaps)
        if args.out:
            Path(args.out).write_text(md, encoding="utf-8")
        print(md)
        raise SystemExit(1 if gaps else 0)
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    main()
