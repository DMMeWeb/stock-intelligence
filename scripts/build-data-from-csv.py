import csv
import json
import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUTPUT = ROOT / "public" / "data.json"
KIDS_SUFFIXES = ("02", "04", "06", "08")


def read_csv(path):
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def clean_text(value):
    return str(value or "").strip()


def clean_sku(value):
    sku = clean_text(value)
    if sku.startswith("'"):
        sku = sku[1:]
    return sku


def to_number(value, default=None):
    text = clean_text(value).replace(",", "")
    if text == "":
        return default
    try:
        return float(text)
    except ValueError:
        return default


def parse_shopify_date(value):
    text = clean_text(value)
    if not text:
        return None

    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass

    # Excel serial dates can appear when a sheet has normalized date cells.
    serial = to_number(text)
    if serial is not None and serial > 20000:
        return datetime.fromordinal(datetime(1899, 12, 30).toordinal() + int(serial))

    return None


def make_specs(row):
    pairs = []
    for idx in ("1", "2", "3"):
        name = clean_text(row.get(f"Option{idx} Name"))
        value = clean_text(row.get(f"Option{idx} Value"))
        if name and value:
            pairs.append(f"{name}: {value}")
    return " - ".join(pairs)


def segment_for_sku(sku):
    return "KIDS" if clean_sku(sku).endswith(KIDS_SUFFIXES) else "WOMENS"


def build_product_lookup(products):
    lookup = {}
    for row in products:
        sku = clean_sku(row.get("Variant SKU"))
        if not sku:
            continue

        lookup[sku] = {
            "costPrice": to_number(row.get("Cost per item")),
            "variantPrice": to_number(row.get("Variant Price")),
            "title": clean_text(row.get("Title")),
            "handle": clean_text(row.get("Handle")),
            "status": clean_text(row.get("Status")),
        }
    return lookup


def build_order_stats(orders):
    dates = [parse_shopify_date(row.get("Created at")) for row in orders]
    dates = [dt for dt in dates if dt]
    as_of = max(date.today(), max((dt.date() for dt in dates), default=date.today()))

    stats = defaultdict(lambda: {
        "lineCount7": 0,
        "lineCount30": 0,
        "lineCount90": 0,
        "quantity7": 0,
        "quantity30": 0,
        "quantity90": 0,
        "prices": [],
    })

    for row in orders:
        sku = clean_sku(row.get("Lineitem sku"))
        if not sku:
            continue

        created = parse_shopify_date(row.get("Created at"))
        if not created:
            continue

        quantity = to_number(row.get("Lineitem quantity"), 0) or 0
        price = to_number(row.get("Lineitem price"))
        if price is not None:
            stats[sku]["prices"].append(price)

        age_days = (as_of - created.date()).days
        if age_days <= 7:
            stats[sku]["lineCount7"] += 1
            stats[sku]["quantity7"] += quantity
        if age_days <= 30:
            stats[sku]["lineCount30"] += 1
            stats[sku]["quantity30"] += quantity
        if age_days <= 90:
            stats[sku]["lineCount90"] += 1
            stats[sku]["quantity90"] += quantity

    return stats, as_of


def average(values):
    return sum(values) / len(values) if values else None


def rounded(value, digits=4):
    if value is None:
        return None
    return round(value, digits)


def build_items(inventory, products, orders):
    product_lookup = build_product_lookup(products)
    order_stats, as_of = build_order_stats(orders)
    items = []

    for row in inventory:
        sku = clean_sku(row.get("SKU"))
        if not sku:
            continue

        product = product_lookup.get(sku, {})
        stats = order_stats.get(sku, {})
        stock_now = to_number(row.get("On hand (current)"), 0) or 0
        sales7 = stats.get("quantity7", 0)
        sales30 = stats.get("quantity30", 0)
        sales90 = stats.get("quantity90", 0)
        average_sales_price = average(stats.get("prices", []))
        cost_price = product.get("costPrice")

        gross_margin = None
        net_margin = None
        if average_sales_price and cost_price is not None:
            gross_margin = (average_sales_price - cost_price) / average_sales_price
            net_price = average_sales_price * 0.8
            if net_price:
                net_margin = (net_price - cost_price) / net_price

        weeks_until_out = None
        if sales90:
            weeks_until_out = round(stock_now / (sales90 / 13), 1)

        items.append({
            "product": clean_text(row.get("Title")),
            "specs": make_specs(row),
            "segment": segment_for_sku(sku),
            "costPrice": rounded(cost_price, 2),
            "sku": sku,
            "stock30DaysAgo": stock_now + sales30 if sales30 or stock_now else 0,
            "averageSalesPrice": rounded(average_sales_price, 2),
            "sales7": sales7,
            "sales30": sales30,
            "sales90": sales90,
            "grossMargin": rounded(gross_margin),
            "netMargin": rounded(net_margin),
            "stockNow": stock_now,
            "weeksUntilOut": weeks_until_out,
            "orderLineCounts": {
                "sales7": stats.get("lineCount7", 0),
                "sales30": stats.get("lineCount30", 0),
                "sales90": stats.get("lineCount90", 0),
            },
        })

    return items, as_of


def main():
    inventory = read_csv(DATA_DIR / "inventory.csv")
    orders = read_csv(DATA_DIR / "orders.csv")
    products = read_csv(DATA_DIR / "products.csv")
    items, as_of = build_items(inventory, products, orders)

    payload = {
        "generatedFrom": "CSV exports",
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "asOfDate": as_of.isoformat(),
        "counts": {
            "dataItems": len(items),
            "!Inventory": len(inventory),
            "!Orders": len(orders),
            "!Products": len(products),
        },
        "calculationNotes": {
            "salesCounts": "Uses Lineitem quantity summed by SKU over 7/30/90 days.",
            "averageSalesPrice": "Simple average of Lineitem price by SKU.",
            "netMargin": "Uses average sales price multiplied by 0.8 before subtracting cost price.",
            "segment": "KIDS if SKU ends 02, 04, 06, or 08. All other SKUs are WOMENS.",
        },
        "items": items,
        "reports": [],
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"))

    kids = sum(1 for item in items if item["segment"] == "KIDS")
    with_margin = sum(1 for item in items if item["netMargin"] is not None)
    print(json.dumps({
        "items": len(items),
        "orders": len(orders),
        "products": len(products),
        "asOfDate": payload["asOfDate"],
        "kids": kids,
        "womens": len(items) - kids,
        "withMargin": with_margin,
    }, indent=2))


if __name__ == "__main__":
    main()
