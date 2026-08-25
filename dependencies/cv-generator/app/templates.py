"""CV renderer — the six target-role templates.

Templates are pure config: they only change section order, headings and
labels. No content is invented here — every string shown comes from the
``snapshot`` dict built by ``snapshot.py`` and is ``html.escape``d before
output.

``render(snapshot, role)`` returns a fully self-contained HTML document (inline
print CSS, A4 ``@page``, no external assets) so the browser's print-to-PDF
produces a clean CV.
"""

from html import escape

try:
    import segno
except ImportError:  # pragma: no cover - optional dependency at build time
    segno = None

from app.config import FRONTEND_PUBLIC_URL


# ---------------------------------------------------------------------------
# Role templates
# ---------------------------------------------------------------------------

# Shared section order for the Minimal Professional (Variant A) layout:
# Summary, Technical Skills, Experience, Projects, Certificates, Languages.
_ROLE_ORDER = [
    "summary",
    "skills",
    "experience",
    "projects",
    "certificates",
    "languages",
]

CV_SECTIONS = (
    "summary",
    "contact",
    "skills",
    "projects",
    "languages",
    "experience",
    "certificates",
)

ROLE_TEMPLATES = {
    "backend-developer": {
        "section_order": _ROLE_ORDER,
        "summary_label": "Summary",
        "skills_grouping": "category",
        "contact_labels": {
            "email": "Email",
            "github": "GitHub",
            "linkedin": "LinkedIn",
            "phone": "Phone",
            "website": "Website",
        },
    },
    "frontend-developer": {
        "section_order": _ROLE_ORDER,
        "summary_label": "Summary",
        "skills_grouping": "category",
        "contact_labels": {
            "email": "Email",
            "github": "GitHub",
            "linkedin": "LinkedIn",
            "phone": "Phone",
            "website": "Website",
        },
    },
    "full-stack-developer": {
        "section_order": _ROLE_ORDER,
        "summary_label": "Summary",
        "skills_grouping": "category",
        "contact_labels": {
            "email": "Email",
            "github": "GitHub",
            "linkedin": "LinkedIn",
            "phone": "Phone",
            "website": "Website",
        },
    },
    "machine-learning-engineer": {
        "section_order": _ROLE_ORDER,
        "summary_label": "Profile",
        "skills_grouping": "category",
        "contact_labels": {
            "email": "Email",
            "github": "GitHub",
            "linkedin": "LinkedIn",
            "phone": "Phone",
            "website": "Website",
        },
    },
    "data-scientist": {
        "section_order": _ROLE_ORDER,
        "summary_label": "Profile",
        "skills_grouping": "category",
        "contact_labels": {
            "email": "Email",
            "github": "GitHub",
            "linkedin": "LinkedIn",
            "phone": "Phone",
            "website": "Website",
        },
    },
    "devops-engineer": {
        "section_order": _ROLE_ORDER,
        "summary_label": "Summary",
        "skills_grouping": "category",
        "contact_labels": {
            "email": "Email",
            "github": "GitHub",
            "linkedin": "LinkedIn",
            "phone": "Phone",
            "website": "Website",
        },
    },
}

# Section display names used when the config doesn't pin one down.
_SECTION_NAMES = {
    "summary": "Summary",
    "contact": "Contact",
    "skills": "Technical Skills",
    "projects": "Projects",
    "languages": "Languages",
    "experience": "Experience",
    "certificates": "Certificates",
}

# The only URL schemes allowed to become an ``href`` — anything else
# (javascript:, data:, vbscript:, ...) is dropped from the CV entirely.
_SAFE_URL_SCHEMES = ("http://", "https://", "mailto:", "tel:")

# Map incoming skill categories to the five CV skill groups, keeping a stable
# display order. Everything else falls into the "Tools" bucket.
_SKILL_CATEGORY_GROUPS = {
    "programming_languages": ("Languages", 0),
    "languages": ("Languages", 0),
    "frameworks": ("Frameworks", 1),
    "libraries": ("Frameworks", 1),
    "databases": ("Databases", 2),
    "devops": ("DevOps/Cloud", 3),
    "cloud": ("DevOps/Cloud", 3),
    "machine_learning": ("Tools", 4),
    "mobile": ("Tools", 4),
    "ui_ux": ("Tools", 4),
    "soft_skills": ("Tools", 4),
    "tools": ("Tools", 4),
}


def _skill_group_info(category: str) -> tuple[str, int]:
    """Return the canonical CV group label and sort priority for a category."""
    cat = (category or "").strip().lower()
    return _SKILL_CATEGORY_GROUPS.get(cat, ("Tools", 4))


def _is_safe_href(url: str) -> bool:
    lowered = url.strip().lower()
    return any(lowered.startswith(scheme) for scheme in _SAFE_URL_SCHEMES)


def _esc(value) -> str:
    """``html.escape`` any user-controlled value (safe for '' / None / numbers)."""
    return escape("" if value is None else str(value))


def _humanize_role(role: str) -> str:
    return " ".join(p.capitalize() for p in (role or "").split("-"))


def _qr_svg(url: str, scale: int = 3) -> str:
    """Inline SVG QR code for ``url`` (empty string when segno is unavailable)."""
    if not url or segno is None:
        return ""
    try:
        qr = segno.make(url, error="m", boost_error=False)
        svg = qr.svg_inline(scale=scale, dark="#1a1a1a", light="#ffffff")
        return svg
    except Exception:  # pragma: no cover - never break the CV over a QR failure
        return ""


def _profile_public_url(email: str) -> str:
    """The frontend public profile URL for ``email`` (QR target)."""
    slug = (email or "").split("@")[0].strip()
    if not slug:
        return ""
    base = (FRONTEND_PUBLIC_URL or "https://iclass.ai").rstrip("/")
    return f"{base}/profile/{_esc(slug)}"


def _render_date_range(item: dict) -> str:
    start = _esc(item.get("start_date") or "")
    end = "Present" if item.get("present") else _esc(item.get("end_date") or "")
    if start and end:
        return f"{start} — {end}"
    return start or end


def _render_urls(item: dict) -> str:
    urls = item.get("urls") or []
    links = []
    for url in urls:
        url = str(url).strip()
        if not url or not _is_safe_href(url):
            # Drop unsafe schemes (javascript:, data:, ...) outright.
            continue
        href = _esc(url)
        label = url if len(url) < 60 else url[:57] + "..."
        links.append(f'<a href="{href}" rel="noopener">{_esc(label)}</a>')
    return " · ".join(links)


def _contact_link(key: str, value: str) -> str:
    """Render a contact value as a safe link (or plain text if no safe URL can be formed)."""
    v = value.strip()
    if not v:
        return ""
    if key == "email":
        url = f"mailto:{v}"
    elif key == "phone":
        url = f"tel:{v}"
    elif key == "github":
        url = v if v.startswith(("http://", "https://")) else f"https://github.com/{v.lstrip('@')}"
    elif key == "linkedin":
        url = v if v.startswith(("http://", "https://")) else f"https://www.linkedin.com/in/{v.lstrip('@')}"
    elif key == "website":
        url = v if v.startswith(("http://", "https://")) else f"https://{v}"
    else:
        return _esc(v)
    if not _is_safe_href(url):
        return _esc(v)
    return f'<a href="{_esc(url)}" rel="noopener">{_esc(v)}</a>'


def _section_heading(title: str) -> str:
    return f'<h2 class="section-title">{_esc(title)}</h2>'


def _experience_bullets(description: str) -> str:
    """Render a single-line or multi-line description as 1-4 bullet points."""
    if not description:
        return ""
    lines = [line.strip() for line in description.split("\n") if line.strip()]
    if not lines:
        return ""
    # Keep up to four bullets; multi-line descriptions are assumed to be bullets.
    lis = "".join(f"<li>{_esc(line)}</li>" for line in lines[:4])
    return f'<ul class="entry-bullets">{lis}</ul>'


def _section_html(section: str, snapshot: dict, cfg: dict) -> str:
    """Render one section; returns '' when the section has no content."""
    if section == "summary":
        bio = (snapshot.get("summary") or {}).get("bio") or ""
        if not bio:
            return ""
        return (
            _section_heading(cfg.get("summary_label") or "Summary")
            + f'<p class="summary-text">{_esc(bio)}</p>'
        )

    if section == "contact":
        contact = snapshot.get("contact") or {}
        labels = cfg.get("contact_labels") or {}
        entries = []
        for key, value in contact.items():
            value = (value or "").strip()
            if not value:
                continue
            label = labels.get(key) or key.replace("_", " ").capitalize()
            entries.append(f"<strong>{_esc(label)}:</strong> {_esc(value)}")
        if not entries:
            return ""
        return (
            _section_heading("Contact")
            + '<p class="contact-line">'
            + " &nbsp;|&nbsp; ".join(entries)
            + "</p>"
        )

    if section == "skills":
        skills = snapshot.get("skills") or []
        if not skills:
            return ""
        grouping = cfg.get("skills_grouping") or "flat"
        if grouping == "category":
            groups: dict[str, dict] = {}
            for s in skills:
                label, priority = _skill_group_info(s.get("category") or "")
                groups.setdefault(
                    label, {"priority": priority, "items": []}
                )["items"].append(s)
            blocks = []
            for label, data in sorted(groups.items(), key=lambda x: (x[1]["priority"], x[0])):
                chips = "".join(
                    f'<span class="chip">{_esc(i.get("name") or "")}'
                    + (
                        f' <em>{_esc(i.get("level") or "")}</em>'
                        if i.get("level")
                        else ""
                    )
                    + "</span>"
                    for i in data["items"]
                )
                blocks.append(
                    f'<div class="skill-group">'
                    f'<h3 class="group-title">{_esc(label)}</h3>'
                    f'<p class="tags">{chips}</p>'
                    f'</div>'
                )
            return _section_heading("Technical Skills") + "".join(blocks)
        chips = "".join(
            f'<span class="chip">{_esc(s.get("name") or "")}'
            + (f' <em>{_esc(s.get("level") or "")}</em>' if s.get("level") else "")
            + "</span>"
            for s in skills
        )
        return _section_heading("Technical Skills") + f'<p class="tags">{chips}</p>'

    if section == "projects":
        projects = snapshot.get("projects") or []
        if not projects:
            return ""
        blocks = []
        for p in projects:
            title = _esc(p.get("title") or "")
            urls = _render_urls(p)
            dates = _render_date_range(p)
            header = f'<h3 class="entry-title">{title}</h3>'
            if dates:
                header += f'<span class="entry-dates">{dates}</span>'
            body = ""
            bullets = p.get("bullets") or []
            if bullets:
                lis = "".join(f"<li>{_esc(b)}</li>" for b in bullets if str(b).strip())
                body += f'<ul class="entry-bullets">{lis}</ul>'
            if urls:
                body += f'<p class="entry-links">{urls}</p>'
            tags = p.get("skills") or []
            if tags:
                chips = "".join(
                    f'<span class="chip">{_esc(t)}</span>'
                    for t in tags
                    if str(t).strip()
                )
                body += f'<p class="tags">{chips}</p>'
            blocks.append(
                f'<div class="entry">'
                f'<div class="entry-header">{header}</div>'
                f'{body}'
                f'</div>'
            )
        return _section_heading("Projects") + "".join(blocks)

    if section == "experience":
        rows = snapshot.get("experience") or []
        if not rows:
            return ""
        blocks = []
        for e in rows:
            role = _esc(e.get("role") or "")
            company = _esc(e.get("company") or "")
            dates = _render_date_range(e)
            title_parts = [p for p in [role, company] if p]
            title = " · ".join(title_parts)
            head = f'<h3 class="entry-title">{title}</h3>'
            if dates:
                head += f'<span class="entry-dates">{dates}</span>'
            body = ""
            body += _experience_bullets(e.get("description") or "")
            tech = [t for t in (e.get("technologies") or []) if str(t).strip()]
            if tech:
                chips = "".join(f'<span class="chip">{_esc(t)}</span>' for t in tech)
                body += f'<p class="tags">{chips}</p>'
            blocks.append(
                f'<div class="entry">'
                f'<div class="entry-header">{head}</div>'
                f'{body}'
                f'</div>'
            )
        return _section_heading("Experience") + "".join(blocks)

    if section == "certificates":
        rows = snapshot.get("certificates") or []
        if not rows:
            return ""
        items = []
        for c in rows:
            title = _esc(c.get("title") or "")
            issuer = _esc(c.get("issuer") or "")
            issue = _esc(c.get("issue_date") or "")
            text = title
            if issuer:
                text += f" — {issuer}"
            proficiency = f'<span class="list-proficiency">{_esc(issue)}</span>' if issue else ""
            items.append(f'<li><span>{text}</span>{proficiency}</li>')
        return (
            _section_heading("Certificates")
            + f'<ul class="simple-list">{"".join(items)}</ul>'
        )

    if section == "languages":
        rows = snapshot.get("languages") or []
        if not rows:
            return ""
        items = []
        for lang in rows:
            name = _esc(lang.get("name") or "")
            level = _esc(lang.get("level") or "")
            proficiency = f'<span class="list-proficiency">{level}</span>' if level else ""
            items.append(f'<li><span>{name}</span>{proficiency}</li>')
        return (
            _section_heading("Languages")
            + f'<ul class="simple-list">{"".join(items)}</ul>'
        )

    return ""


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def render(snapshot: dict, role: str) -> str:
    """Render a self-contained printable HTML CV from an allow-listed snapshot."""
    cfg = ROLE_TEMPLATES.get(role) or ROLE_TEMPLATES["backend-developer"]

    personal = snapshot.get("personal") or {}
    role_label = snapshot.get("target_role_label") or _humanize_role(role)
    contact = snapshot.get("contact") or {}
    contact_items = []
    for key in ("email", "phone", "github", "linkedin", "website"):
        value = (contact.get(key) or "").strip()
        if value:
            contact_items.append(_contact_link(key, value))
    contact_html = (
        f'<div class="contact-bar">'
        + "".join(f'<span class="contact-item">{item}</span>' for item in contact_items)
        + "</div>"
        if contact_items
        else ""
    )

    parts = [_doc_head()]
    parts.append(
        "<header class=\"head\">"
        f'<h1>{_esc(personal.get("full_name") or "")}</h1>'
        f'<p class="headline-role">{_esc(role_label)}</p>'
        f"{contact_html}"
        "</header>"
    )
    for section in cfg["section_order"]:
        html = _section_html(section, snapshot, cfg)
        if html:
            parts.append(f'<section class="cv-section">{html}</section>')
    parts.append(_footer(snapshot))
    parts.append(_doc_tail())
    return "".join(parts)


def _footer(snapshot: dict) -> str:
    """A footer with the QR code linking to the frontend public profile."""
    contact = snapshot.get("contact") or {}
    email = (contact.get("email") or "").strip()
    url = _profile_public_url(email)
    qr = _qr_svg(url) if url else ""
    if not qr:
        return ""
    return (
        '<footer class="cv-footer">'
        '<div class="footer-left">'
        f'<p class="footer-hint">Scan to view my public profile</p>'
        f'<p class="footer-url">{_esc(url)}</p>'
        "</div>"
        f'<div class="footer-qr">{qr}</div>'
        "</footer>"
    )


# ---------------------------------------------------------------------------
# Document shell + inline print CSS
# ---------------------------------------------------------------------------

_CSS = """
@page { size: A4; margin: 16mm 18mm; }

* { box-sizing: border-box; }

html, body {
  margin: 0;
  padding: 0;
}

body {
  font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  font-size: 10pt;
  line-height: 1.45;
  color: #222222;
  background: #ffffff;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

h1, h2, h3 {
  font-family: inherit;
  font-weight: 600;
  margin: 0;
}

a {
  color: #222222;
  text-decoration: none;
}

a:hover {
  text-decoration: underline;
}

/* Header --------------------------------------------------------------- */
.head {
  margin-bottom: 22px;
  padding-bottom: 14px;
  border-bottom: 1px solid #d0d0d0;
  page-break-inside: avoid;
}

.head h1 {
  font-size: 29pt;
  font-weight: 700;
  line-height: 1.1;
  color: #111111;
  letter-spacing: -0.4px;
  margin-bottom: 6px;
}

.headline-role {
  font-size: 12pt;
  font-weight: 500;
  color: #444444;
  margin: 0 0 10px;
}

.contact-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 18px;
  font-size: 9.5pt;
  color: #333333;
}

.contact-item {
  display: inline-flex;
  align-items: center;
}

.contact-item + .contact-item::before {
  content: "·";
  color: #999999;
  margin-right: 18px;
}

/* Sections ------------------------------------------------------------- */
.cv-section {
  margin-bottom: 18px;
  page-break-inside: auto;
}

.section-title {
  font-size: 10.5pt;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: #111111;
  margin: 0 0 10px;
  padding-bottom: 4px;
  border-bottom: 1px solid #cccccc;
  page-break-after: avoid;
}

.summary-text {
  margin: 0;
  color: #333333;
  line-height: 1.5;
}

/* Entries (Experience / Projects) -------------------------------------- */
.entry {
  margin-bottom: 13px;
  page-break-inside: avoid;
}

.entry:last-child {
  margin-bottom: 0;
}

.entry-header {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: baseline;
  gap: 6px 12px;
  margin-bottom: 3px;
}

.entry-title {
  font-size: 10.5pt;
  font-weight: 600;
  color: #111111;
  line-height: 1.25;
}

.entry-dates {
  font-size: 9pt;
  color: #666666;
  font-weight: 400;
  white-space: nowrap;
}

.entry-bullets {
  margin: 4px 0 0 16px;
  padding: 0;
  color: #333333;
  font-size: 9.5pt;
  line-height: 1.4;
}

.entry-bullets li {
  margin-bottom: 2px;
}

.entry-links {
  font-size: 9pt;
  margin: 4px 0 0;
  color: #444444;
}

/* Skill tags ----------------------------------------------------------- */
.group-title {
  font-size: 9.5pt;
  font-weight: 600;
  color: #111111;
  margin: 0 0 4px;
}

.skill-group {
  margin-bottom: 8px;
}

.skill-group:last-child {
  margin-bottom: 0;
}

.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin: 0;
}

.chip {
  display: inline-block;
  background: #f5f5f5;
  color: #222222;
  border: 1px solid #dddddd;
  border-radius: 2px;
  padding: 2px 7px;
  font-size: 8.5pt;
  font-weight: 500;
  line-height: 1.3;
}

.chip em {
  font-style: normal;
  color: #555555;
  font-weight: 400;
}

/* Simple lists (Certificates / Languages) ------------------------------ */
.simple-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.simple-list li {
  padding: 4px 0;
  border-bottom: 1px solid #eeeeee;
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
}

.simple-list li:last-child {
  border-bottom: none;
}

.list-proficiency {
  color: #666666;
  font-size: 9pt;
  white-space: nowrap;
}

/* Footer --------------------------------------------------------------- */
.cv-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-top: 24px;
  padding-top: 12px;
  border-top: 1px solid #d0d0d0;
  page-break-inside: avoid;
}

.footer-left {
  min-width: 0;
}

.footer-hint {
  margin: 0 0 2px;
  font-size: 8.5pt;
  color: #666666;
}

.footer-url {
  margin: 0;
  font-size: 8.5pt;
  color: #888888;
  word-break: break-all;
}

.footer-qr svg {
  width: 68px;
  height: 68px;
  display: block;
}

/* Print overrides ------------------------------------------------------ */
@media print {
  body { background: #ffffff; }
  .no-print { display: none; }
  a { text-decoration: none; }
  .entry { page-break-inside: avoid; }
  .cv-section { page-break-inside: auto; }
}
"""


def _doc_head() -> str:
    return (
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>Talent CV</title><style>{_CSS}</style></head><body>"
    )


def _doc_tail() -> str:
    return "</body></html>"
