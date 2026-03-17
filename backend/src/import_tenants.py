"""
Import tenants from info.xlsx into the database.

Usage (inside Docker):
    docker compose run --rm backend python -m src.import_tenants
"""

import re
import os
from datetime import datetime
from pathlib import Path

import openpyxl

from src.database import SessionLocal, engine
from src.models import Base, Building, Tenant, User


# --- Cyrillic → Latin normalization ---
CYRILLIC_MAP = str.maketrans("АВСЕНКМОРТХаевскморх", "ABCEHKMOPTXaeвckмopx")


def normalize_text(s: str) -> str:
    """Replace Cyrillic look-alikes with Latin equivalents."""
    return s.translate(CYRILLIC_MAP)


# Known building prefixes (longest first for greedy matching)
KNOWN_BUILDINGS = sorted([
    "ARMAN VILLA",
    "ESENTAI APARTMENTS A",
    "ESENTAI APARTMENTS B",
    "IVANILOVA",
    "SAMAL TOWERS",
    "SAMAL DELUXE",
    "SOLNECHNAYA DOLINA",
    "STOLICHNIY",
    "ZHAMAKAYEV HOUSING",
    "DOSTYK",
    "EXCLUSIVE TIME",
    "SNEGINA",
    "AFD PLAZA",
    "ORION",
    "TAU SHATYR",
], key=len, reverse=True)

# Additional buildings with non-Latin names
EXTRA_BUILDINGS = [
    "ЖК Амир",
    "ЖК Royal",
]

PHONE_RE = re.compile(r"[\+]?[\d][\d\s\-\(\)]{8,}")


def split_building_apartment(obj: str) -> tuple[str, str]:
    """Split 'ESENTAI APARTMENTS A 8C' -> ('ESENTAI APARTMENTS A', '8C')."""
    obj_upper = normalize_text(obj.upper().strip())

    for prefix in KNOWN_BUILDINGS:
        if obj_upper.startswith(prefix):
            apt = obj[len(prefix):].strip()
            return prefix, apt

    # Check non-Latin buildings
    for prefix in EXTRA_BUILDINGS:
        if obj.startswith(prefix):
            apt = obj[len(prefix):].strip()
            return prefix, apt

    # Fallback: entire string is building, no apartment
    return obj.strip(), ""


def extract_phone(text: str) -> str:
    """Extract first phone number from a contact string."""
    if not text:
        return ""
    match = PHONE_RE.search(text)
    if match:
        return match.group(0).strip()
    return ""


def extract_name_from_contact(text: str) -> str:
    """Extract name portion before the phone number."""
    if not text:
        return ""
    match = PHONE_RE.search(text)
    if match:
        name = text[:match.start()].strip().rstrip("+").strip()
        return name
    return text.strip()


def normalize_category(raw: str) -> str:
    """Normalize category: Cyrillic А→A, В→B, etc."""
    raw = raw.strip()
    normalized = normalize_text(raw).upper()
    if normalized in ("A", "B", "C"):
        return normalized
    if "не обслуживаем" in raw.lower() or "не обслуживаем" in raw:
        return "no_service"
    if normalized in ("-", ""):
        return ""
    return raw


def format_date(dt) -> str:
    """Format datetime to 'YYYY-MM-DD' string."""
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d")
    if dt:
        return str(dt)
    return ""


def compute_lease_duration(start, end) -> str:
    """Compute lease duration as 'X months' from start and end dates."""
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        if end:
            return f"до {format_date(end)}"
        return ""
    delta_months = (end.year - start.year) * 12 + (end.month - start.month)
    if delta_months > 0:
        return f"{delta_months} мес. (до {format_date(end)})"
    return f"до {format_date(end)}"


def parse_xlsx(filepath: str) -> list[dict]:
    """Parse info.xlsx and return normalized tenant dicts."""
    wb = openpyxl.load_workbook(filepath)
    ws = wb[wb.sheetnames[0]]

    tenants = []
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, values_only=True):
        obj = row[1]
        if not obj or not str(obj).strip():
            continue

        obj = str(obj).strip()
        building_name, apartment = split_building_apartment(obj)

        lease_start = row[2]
        lease_end = row[3]
        category_raw = str(row[4]).strip() if row[4] else ""
        client_name = str(row[5]).strip() if row[5] else ""
        company = str(row[6]).strip() if row[6] else ""
        primary_contact = str(row[7]).strip() if row[7] else ""
        extra_contact = str(row[8]).strip() if row[8] else ""

        # Normalize
        category = normalize_category(category_raw)
        phone = extract_phone(primary_contact)

        # Fallback: try extracting phone from extra contact
        if not phone and extra_contact:
            phone = extract_phone(extra_contact)

        # Tenant name: prefer client column, fallback to contact name
        name = client_name if client_name and client_name != "-----" else ""
        if not name:
            name = extract_name_from_contact(primary_contact)
        if not name:
            name = "Неизвестный"

        # Clean up company
        if company in ("-----", "None", ""):
            company = ""

        # Build notes
        notes_parts = []
        if category:
            notes_parts.append(f"Категория: {category}")
        if company:
            notes_parts.append(f"Компания: {company}")
        notes = " | ".join(notes_parts) if notes_parts else ""

        tenants.append({
            "building_name": building_name,
            "apartment": apartment,
            "name": name,
            "phone": phone,
            "move_in_date": format_date(lease_start),
            "lease_duration": compute_lease_duration(lease_start, lease_end),
            "emergency_contact": extra_contact if extra_contact and extra_contact != "None" else "",
            "notes": notes,
            "agent_enabled": False,
        })

    return tenants


def import_tenants():
    """Main import function."""
    xlsx_path = os.path.join(os.path.dirname(__file__), "..", "info.xlsx")
    # Docker workdir is /app, so also check there
    if not Path(xlsx_path).exists():
        xlsx_path = "/app/info.xlsx"
    if not Path(xlsx_path).exists():
        print(f"ERROR: {xlsx_path} not found")
        return

    print("Parsing info.xlsx...")
    rows = parse_xlsx(xlsx_path)
    print(f"Parsed {len(rows)} tenant rows")

    db = SessionLocal()
    try:
        # Look up Zhanna as building owner
        zhanna = db.query(User).filter(User.email == "zhanna@cosmorent.kz").first()
        if not zhanna:
            print("ERROR: User zhanna@cosmorent.kz not found. Run seed_db first.")
            return
        owner_id = zhanna.id

        # Collect unique buildings
        building_names = sorted(set(r["building_name"] for r in rows))
        print(f"Found {len(building_names)} unique buildings")

        # Create or find buildings
        building_map: dict[str, int] = {}
        buildings_created = 0
        for bname in building_names:
            existing = db.query(Building).filter(Building.name == bname).first()
            if existing:
                building_map[bname] = existing.id
            else:
                b = Building(name=bname, address=bname, owner_id=owner_id)
                db.add(b)
                db.flush()
                building_map[bname] = b.id
                buildings_created += 1

        # Import tenants
        tenants_created = 0
        tenants_skipped = 0
        for r in rows:
            bid = building_map[r["building_name"]]

            # Skip if tenant already exists for this building + apartment
            existing = db.query(Tenant).filter(
                Tenant.building_id == bid,
                Tenant.apartment == r["apartment"],
            ).first()
            if existing:
                tenants_skipped += 1
                continue

            t = Tenant(
                name=r["name"],
                phone=r["phone"],
                building_id=bid,
                apartment=r["apartment"],
                move_in_date=r["move_in_date"] or None,
                lease_duration=r["lease_duration"] or None,
                emergency_contact=r["emergency_contact"] or None,
                notes=r["notes"] or None,
                agent_enabled=r["agent_enabled"],
            )
            db.add(t)
            tenants_created += 1

        db.commit()

        print(f"\n=== Import Summary ===")
        print(f"Buildings created: {buildings_created}")
        print(f"Tenants imported:  {tenants_created}")
        print(f"Tenants skipped (duplicate phone): {tenants_skipped}")
        print("Done!")

    finally:
        db.close()


if __name__ == "__main__":
    import_tenants()
