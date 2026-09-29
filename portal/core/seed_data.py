"""
The real MCC roster and engine configuration.

Ported from `server/src/admin/realData.ts`, which was the single source of truth
for the 41 requesting bodies and 23 media-team members. Editing this file and
re-running `manage.py seed_real_data` is how the roster changes in bulk; the
Admin view and Django admin handle day-to-day single edits.

Kept as plain data separate from the command that loads it, so it is easy to
scan and diff — the roster is the part people actually review.
"""

from core.constants import VERTICAL_SKILLS

ADMIN_EMAILS = ["mbatm25010@iimsirmaur.ac.in"]
SECRETARY_EMAILS = ["mba25114@iimsirmaur.ac.in"]

ALLOWED_DOMAINS = ["iimsirmaur.ac.in"]


# (login email, name, acronym, type, campus)
COMMITTEES = [
    ("sapient@iimsirmaur.ac.in", "Sapient", "SPT", "Club", "MBA Campus"),
    ("finserve@iimsirmaur.ac.in", "Finserve", "FIN", "Club", "MBA Campus"),
    ("scope@iimsirmaur.ac.in", "Scope – The Operations Club", "SOC", "Club", "MBA Campus"),
    ("rangmanch@iimsirmaur.ac.in", "RangManch", "RM", "Club", "MBA Campus"),
    ("datonics@iimsirmaur.ac.in", "Datonics", "DATA", "Club", "MBA Campus"),
    ("horizon@iimsirmaur.ac.in", "HORIZON – The HR Club", "HR", "Club", "MBA Campus"),
    ("atithya@iimsirmaur.ac.in", "Atithya (Tourism and Hospitality Club)", "ATH", "Club", "MBA Campus"),
    ("markaizen@iimsirmaur.ac.in", "Markaizen", "MRKZ", "Club", "MBA Campus"),
    ("mosaic@iimsirmaur.ac.in", "Mosaic", "MOSC", "Club", "MBA Campus"),
    ("ebsb@iimsirmaur.ac.in", "EBSB (Ek Bharat Shreshtha Bharat Club)", "EBSB", "Club", "MBA Campus"),
    ("jal@iimsirmaur.ac.in", "JAL", "JAL", "Club", "MBA Campus"),
    ("mediacell@iimsirmaur.ac.in", "Media & Communications Committee", "MCC", "Committee", "MBA Campus"),
    ("sac@iimsirmaur.ac.in", "Student Academic Committee (SAC)", "SAC", "Committee", "MBA Campus"),
    ("campusconnect@iimsirmaur.ac.in", "Admissions Committee", "ADCOM", "Committee", "MBA Campus"),
    ("industrialrelation@iimsirmaur.ac.in", "Industrial Relations and Sponsorship Committee", "IRS", "Committee", "MBA Campus"),
    ("trainingcell@iimsirmaur.ac.in", "Training and Development Committee", "TND", "Committee", "MBA Campus"),
    ("alumni@iimsirmaur.ac.in", "Alumni Relations Committee", "ARC", "Committee", "MBA Campus"),
    ("sportscommittee@iimsirmaur.ac.in", "Sports Committee", "SCOM", "Committee", "MBA Campus"),
    ("sankalp@iimsirmaur.ac.in", "Sankalp – Corporate Social Responsibility Cell", "CSR", "Committee", "MBA Campus"),
    ("enicell@iimsirmaur.ac.in", "Entrepreneurship and Incubation Cell", "ENI", "Committee", "MBA Campus"),
    ("sanskriti@iimsirmaur.ac.in", "Sanskriti – Cultural Committee", "CULCOM", "Committee", "MBA Campus"),
    ("infra-it@iimsirmaur.ac.in", "Infrastructure and IT Committee", "INFRA", "Committee", "MBA Campus"),
    ("messcommittee@iimsirmaur.ac.in", "Naivedyam (Mess Committee)", "MESS", "Committee", "MBA Campus"),
    ("placements@iimsirmaur.ac.in", "Corporate Relations & Placement Committee", "PCOM", "Committee", "MBA Campus"),
    ("xentrixesports@iimsirmaur.ac.in", "Xentrix", "XNTRX", "SIG", "MBA Campus"),
    ("pgpoffice@iimsirmaur.ac.in", "PGP Office", "PGP", "Office", "MBA Campus"),
    ("mdpoffice@iimsirmaur.ac.in", "MDP Office", "MDP", "Office", "MBA Campus"),
    ("bms.infra-it@iimsirmaur.ac.in", "Infra-IT Committee (BMS)", "INFRABMS", "Committee", "BMS Campus"),
    ("sacbms@iimsirmaur.ac.in", "Students' Academic committee (BMS)", "SACBMS", "Committee", "BMS Campus"),
    ("bmspcom@iimsirmaur.ac.in", "Placement Committee (BMS)", "PCOMBMS", "Committee", "BMS Campus"),
    ("bmsmesscommittee@iimsirmaur.ac.in", "Naivedyam (BMS Mess Committee)", "BMSMESS", "Committee", "BMS Campus"),
    ("sportscommittee.bms@iimsirmaur.ac.in", "BMS Sports Committee", "BMSSPORTS", "Committee", "BMS Campus"),
    ("culturalcommittee.bms@iimsirmaur.ac.in", "Kalakriti (BMS Cultural Committee)", "CULCOMBMS", "Committee", "BMS Campus"),
    ("catalyst.x@iimsirmaur.ac.in", "CatalyStX", "CTX", "Club", "BMS Campus"),
    ("econyx@iimsirmaur.ac.in", "Econyx", "ECO", "Club", "BMS Campus"),
    ("hriday@iimsirmaur.ac.in", "HRiday", "HRBMS", "Club", "BMS Campus"),
    ("finexus@iimsirmaur.ac.in", "Finexus", "FINBMS", "Club", "BMS Campus"),
    ("markeista@iimsirmaur.ac.in", "Markeista", "MARBMS", "Club", "BMS Campus"),
    ("synapsys@iimsirmaur.ac.in", "Synapsys", "SNP", "Club", "BMS Campus"),
    ("synex@iimsirmaur.ac.in", "SynEx", "SNX", "Club", "BMS Campus"),
    ("spicmacay@iimsirmaur.ac.in", "Spic Macay", "SM", "Club", "BMS Campus"),
]


#: Second-years ("seniors") only ever supervise: they are eligible for the Task
#: Supervisor role and nothing else. First-years ("execs") do all the hands-on
#: work — photography, videography, editing, writing, design, and coordinating
#: an event. (engine/assign.py is the one place that rule is enforced.)
SENIOR = 2
EXEC = 1

# (email, name, vertical, campus, year, skills) — the original roster. No
# secondary vertical or phone on record for these; both start blank.
_EXISTING_TEAM = [
    ("mba25178@iimsirmaur.ac.in", "Sanjana Jaiswal", "Graphic Designs", "MBA Campus", SENIOR, ["Graphic design"]),
    ("mba25189@iimsirmaur.ac.in", "Aisha Firdouse", "Graphic Designs", "MBA Campus", SENIOR, ["Graphic design", "Video Editing", "Photography", "Videography"]),
    ("mbatm25033@iimsirmaur.ac.in", "Priyal Shende", "Content Writing", "MBA Campus", SENIOR, ["Content Writing", "Photography"]),
    ("mbatm25024@iimsirmaur.ac.in", "Navina", "Photography", "MBA Campus", SENIOR, ["Photography"]),
    ("mba25092@iimsirmaur.ac.in", "Siddarth N", "Photography", "MBA Campus", SENIOR, ["Photography", "Photo Editing"]),
    ("mbatthm25017@iimsirmaur.ac.in", "Suda Yugandhar", "Photography", "MBA Campus", SENIOR, ["Photography", "Photo Editing", "Videography", "Content Writing", "Video Editing"]),
    ("mba25109@iimsirmaur.ac.in", "G N V Umanand Naik", "Photography", "MBA Campus", SENIOR, ["Photography", "Photo Editing"]),
    ("mba25114@iimsirmaur.ac.in", "Kamalasegaran A", "Photography", "MBA Campus", SENIOR, ["Photography", "Photo Editing", "Videography", "Content Writing"]),
    ("mbatm25010@iimsirmaur.ac.in", "Agrim Kaundal", "Photography", "MBA Campus", SENIOR, ["Photography", "Photo Editing"]),
    ("mba25159@iimsirmaur.ac.in", "Ishan Negi", "Photography", "MBA Campus", SENIOR, ["Photography", "Photo Editing", "Videography"]),
    ("bms25021@iimsirmaur.ac.in", "Arya Paliwal", "Photography", "BMS Campus", EXEC, ["Photography", "Videography"]),
    ("bms25123@iimsirmaur.ac.in", "Sukriti Saxena", "Videography", "BMS Campus", EXEC, ["Videography", "Graphic design"]),
    ("bms25088@iimsirmaur.ac.in", "Nitya Jaiswal", "Photography", "BMS Campus", EXEC, ["Photography", "Content Writing"]),
    ("bms24006@iimsirmaur.ac.in", "Aditi Shukla", "", "BMS Campus", EXEC, ["Photography", "Graphic design", "Content Writing"]),
    ("bms24107@iimsirmaur.ac.in", "Saguna Rishi", "Content Writing", "BMS Campus", EXEC, ["Content Writing", "Graphic design"]),
    ("bms24002@iimsirmaur.ac.in", "Aarushi Dubey", "", "BMS Campus", EXEC, []),
    ("bms24092@iimsirmaur.ac.in", "Prashant Kumar", "", "BMS Campus", EXEC, []),
    ("bms25077@iimsirmaur.ac.in", "Laasya Nekkanti", "", "BMS Campus", EXEC, []),
    ("bms24007@iimsirmaur.ac.in", "Aditya Kalyankar", "", "BMS Campus", EXEC, []),
    ("bms24141@iimsirmaur.ac.in", "Ujjwala Naudiyal", "", "BMS Campus", EXEC, []),
    ("bms24104@iimsirmaur.ac.in", "Rishabh Garg", "", "BMS Campus", EXEC, []),
    ("bms24150@iimsirmaur.ac.in", "Yash Tripathi", "", "BMS Campus", EXEC, []),
    ("bms24036@iimsirmaur.ac.in", "Deepshikha Das", "", "BMS Campus", EXEC, []),
]


# The 2026 first-year intake (MBA Campus), from the "Team DATA" sheet:
# (email, name, primary vertical, secondary vertical, phone). Their skills are
# derived from BOTH verticals — see `derived_skills`.
_FIRST_YEARS_2026 = [
    ("mba26074@iimsirmaur.ac.in", "Aishwarya Arun Patil", "Photography", "Videography", "9130251222"),
    ("mba26254@iimsirmaur.ac.in", "Pragyi", "Photography", "Content Writing", "8292147297"),
    ("mbatm26019@iimsirmaur.ac.in", "Muhammed Afthab", "Photography", "Videography", "8606770779"),
    ("mba26154@iimsirmaur.ac.in", "Ishan Dubey", "Videography", "Photography", "7489890397"),
    ("mba26020@iimsirmaur.ac.in", "DEEPANSHU KUMAR", "Photography", "Videography", "9315981547"),
    ("mba26106@iimsirmaur.ac.in", "Om Shinde", "Graphic Designs", "Photography", "8080150550"),
    ("mba26045@iimsirmaur.ac.in", "Piyush sharma", "Photography", "Graphic Designs", "9149448470"),
    ("mbatm26034@iimsirmaur.ac.in", "Shruti Randhe", "Videography", "Photography", "9156516558"),
    ("mbatm26036@iimsirmaur.ac.in", "Jaspreet Kaur", "Photography", "Videography", "9817906505"),
    ("mba26048@iimsirmaur.ac.in", "Priyanshi Mehta", "Content Writing", "Videography", "9671436175"),
    ("mba26209@iimsirmaur.ac.in", "Gunjan Yadav", "Graphic Designs", "Photography", "9899010125"),
    ("mba26142@iimsirmaur.ac.in", "Ayush", "Photography", "Videography", "8287430993"),
    ("mba26067@iimsirmaur.ac.in", "Pavan Vaghela", "Photography", "Videography", "9427334693"),
    ("mbatm26002@iimsirmaur.ac.in", "Aditya Kumar", "Videography", "Photography", "9971082340"),
]


def derived_skills(*verticals: str) -> list[str]:
    """The skills that working in these verticals brings, in order, without repeats."""
    skills: list[str] = []
    for vertical in verticals:
        for skill in VERTICAL_SKILLS.get(vertical, []):
            if skill not in skills:
                skills.append(skill)
    return skills


def _member(email, name, vertical, secondary_vertical, campus, year, skills, phone) -> dict:
    return {
        "email": email,
        "name": name,
        "vertical": vertical,
        "secondary_vertical": secondary_vertical,
        "campus": campus,
        "year": year,
        "skills": list(skills),
        "phone": phone,
    }


#: The full roster the seed command loads, one dict per member.
TEAM = [
    _member(email, name, vertical, "", campus, year, skills, "")
    for email, name, vertical, campus, year, skills in _EXISTING_TEAM
] + [
    _member(email, name, primary, secondary, "MBA Campus", EXEC, derived_skills(primary, secondary), phone)
    for email, name, primary, secondary, phone in _FIRST_YEARS_2026
]


# (task, required_skill, points, sla_hours, at_event, requestable, internal_assignable, vertical)
TASK_TYPES = [
    ("Photographer", "Photography", 5, 0, True, True, True, "Photography"),
    ("Videographer", "Videography", 8, 0, True, True, True, "Videography"),
    ("Content Writer", "Content Writing", 3, 12, True, False, True, "Content Writing"),
    ("Photo Editor", "Photo Editing", 3, 24, False, False, True, "Photography"),
    ("Video Editor", "Video Editing", 5, 48, False, False, True, "Videography"),
    # Kept only so requests accepted before the Vetter was dropped still resolve
    # it; nobody can add one by hand any more (internal_assignable=False) and no
    # new request creates one.
    ("Vetter", "Vetting", 2, 24, False, False, False, ""),
    # Any active first-year can coordinate an event — no skill required.
    ("Event Coordinator", "", 4, 0, True, False, True, ""),
    ("Graphic Designer", "Graphic design", 5, 24, False, False, True, "Graphic Designs"),
    # A second-year overseeing one Coverage request: no skill, no points, no
    # deadline; closes when the Event Coordinator marks the event done.
    ("Task Supervisor", "", 0, 0, False, False, True, ""),
]

SLOTS = ["11:00", "14:00", "17:00"]

# (platform, handler_email, points, active)
# NOTE: these handlers are placeholders (docs/PIC.md §5) — no individual owns the
# institute's social accounts in the portal yet. Whoever actually runs them
# should replace mediacell@ here or in Django admin.
PLATFORMS = [
    ("Instagram", "mediacell@iimsirmaur.ac.in", 2, True),
    ("LinkedIn", "mediacell@iimsirmaur.ac.in", 2, True),
    ("X", "mediacell@iimsirmaur.ac.in", 2, True),
]

POINTS_SCHEME = {
    "coordinator_points": 20,
    "domain_task_points": 10,
    "vetter_points": 10,
    "early_window_hours": 24,
    "early_bonus_pct": 30,
    "late_threshold_hours": 48,
    "late_penalty_pct": 30,
    "subsequent_delay_hours": 6,
    "subsequent_penalty_pct": 10,
}

SETTINGS = {
    "sla_hours": 48,
    "campus_strict": True,
    "require_approval_always": False,
    "head_email": "",
    "committee_name": "Media & Communications Committee",
    "default_acronym": "MEDIA",
}
