import json
import os
import time
import urllib.error
import urllib.request
import urllib.parse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "public" / "data.json"
RAW_DIR = ROOT / "data" / "shopify-raw"
KIDS_SUFFIXES = ("02", "04", "06", "08")


def load_env():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def require_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing {name}. Create .env from .env.example and add your Shopify details.")
    return value


def optional_env(name):
    return os.environ.get(name, "").strip()


def clean_sku(value):
    sku = str(value or "").strip()
    return sku[1:] if sku.startswith("'") else sku


def to_float(value, default=None):
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def rounded(value, digits=4):
    if value is None:
        return None
    return round(value, digits)


def average(values):
    return sum(values) / len(values) if values else None


def parse_date(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def segment_for_sku(sku):
    return "KIDS" if clean_sku(sku).endswith(KIDS_SUFFIXES) else "WOMENS"


def option_specs(selected_options):
    pairs = []
    for option in selected_options or []:
        name = str(option.get("name") or "").strip()
        value = str(option.get("value") or "").strip()
        if name and value:
            pairs.append(f"{name}: {value}")
    return " - ".join(pairs)


def quantity_map(inventory_level):
    quantities = {}
    for quantity in inventory_level.get("quantities") or []:
        quantities[quantity.get("name")] = quantity.get("quantity") or 0
    return quantities


class ShopifyClient:
    def __init__(self):
        load_env()
        self.store = require_env("SHOPIFY_STORE").replace("https://", "").replace("http://", "").strip("/")
        self.static_token = optional_env("SHOPIFY_ADMIN_ACCESS_TOKEN")
        self.client_id = optional_env("SHOPIFY_CLIENT_ID")
        self.client_secret = optional_env("SHOPIFY_CLIENT_SECRET")
        self.token = self.static_token
        self.token_expires_at = 0
        if not self.static_token and not (self.client_id and self.client_secret):
            raise SystemExit(
                "Missing credentials. Set SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET in .env "
                "from the Dev Dashboard Settings page, or set SHOPIFY_ADMIN_ACCESS_TOKEN for a legacy app."
            )
        self.api_version = os.environ.get("SHOPIFY_API_VERSION", "2026-04")
        self.url = f"https://{self.store}/admin/api/{self.api_version}/graphql.json"

    def get_token(self):
        if self.static_token:
            return self.static_token
        if self.token and time.time() < self.token_expires_at - 60:
            return self.token

        token_url = f"https://{self.store}/admin/oauth/access_token"
        body = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }).encode("utf-8")
        request = urllib.request.Request(
            token_url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise SystemExit(f"Shopify token request HTTP {exc.code}: {detail}") from exc

        self.token = payload["access_token"]
        self.token_expires_at = time.time() + int(payload.get("expires_in", 86400))
        scopes = payload.get("scope", "")
        print(f"Authenticated with scopes: {scopes}")
        return self.token

    def graphql(self, query, variables=None):
        payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Access-Token": self.get_token(),
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise SystemExit(f"Shopify API HTTP {exc.code}: {detail}") from exc

        if body.get("errors"):
            raise SystemExit(json.dumps(body["errors"], indent=2))

        throttle = (((body.get("extensions") or {}).get("cost") or {}).get("throttleStatus") or {})
        currently_available = throttle.get("currentlyAvailable")
        restore_rate = throttle.get("restoreRate") or 50
        if currently_available is not None and currently_available < 100:
            time.sleep(max(1, (100 - currently_available) / restore_rate))

        return body["data"]


INVENTORY_ITEMS_QUERY = """
query InventoryItems($after: String) {
  inventoryItems(first: 100, after: $after) {
    nodes {
      id
      sku
      unitCost { amount currencyCode }
      countryCodeOfOrigin
      harmonizedSystemCode
      inventoryLevels(first: 20) {
        nodes {
          location { id }
          quantities(names: ["incoming", "committed", "available", "on_hand"]) {
            name
            quantity
          }
        }
      }
      variant {
        id
        title
        price
        selectedOptions { name value }
        product {
          id
          handle
          title
          status
          vendor
          productType
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


ORDERS_QUERY = """
query Orders($after: String, $query: String!) {
  orders(first: 50, after: $after, query: $query, sortKey: CREATED_AT, reverse: true) {
    nodes {
      id
      name
      createdAt
      cancelledAt
      displayFinancialStatus
      displayFulfillmentStatus
      lineItems(first: 100) {
        nodes {
          sku
          name
          quantity
          originalUnitPriceSet {
            shopMoney { amount currencyCode }
          }
          discountedUnitPriceSet {
            shopMoney { amount currencyCode }
          }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def fetch_all_inventory(client):
    nodes = []
    after = None
    while True:
        data = client.graphql(INVENTORY_ITEMS_QUERY, {"after": after})
        page = data["inventoryItems"]
        nodes.extend(page["nodes"])
        print(f"Fetched inventory items: {len(nodes)}")
        if not page["pageInfo"]["hasNextPage"]:
            return nodes
        after = page["pageInfo"]["endCursor"]


def fetch_recent_orders(client):
    lookback_days = int(os.environ.get("SHOPIFY_ORDER_LOOKBACK_DAYS", "92"))
    since = (date.today() - timedelta(days=lookback_days)).isoformat()
    query = f"created_at:>={since} -status:cancelled"
    nodes = []
    after = None
    while True:
        data = client.graphql(ORDERS_QUERY, {"after": after, "query": query})
        page = data["orders"]
        nodes.extend(page["nodes"])
        print(f"Fetched orders: {len(nodes)}")
        if not page["pageInfo"]["hasNextPage"]:
            return nodes
        after = page["pageInfo"]["endCursor"]


def summarize_orders(orders, as_of):
    stats = defaultdict(lambda: {
        "quantity7": 0,
        "quantity30": 0,
        "quantity90": 0,
        "lineCount7": 0,
        "lineCount30": 0,
        "lineCount90": 0,
        "prices": [],
    })

    for order in orders:
        created = parse_date(order.get("createdAt"))
        if not created:
            continue
        age_days = (as_of - created.date()).days
        for line in (order.get("lineItems") or {}).get("nodes") or []:
            sku = clean_sku(line.get("sku"))
            if not sku:
                continue
            quantity = line.get("quantity") or 0
            price = to_float((((line.get("originalUnitPriceSet") or {}).get("shopMoney") or {}).get("amount")))
            if price is not None:
                stats[sku]["prices"].append(price)
            if age_days <= 7:
                stats[sku]["quantity7"] += quantity
                stats[sku]["lineCount7"] += 1
            if age_days <= 30:
                stats[sku]["quantity30"] += quantity
                stats[sku]["lineCount30"] += 1
            if age_days <= 90:
                stats[sku]["quantity90"] += quantity
                stats[sku]["lineCount90"] += 1

    return stats


def build_items(inventory_items, orders):
    dates = [parse_date(order.get("createdAt")) for order in orders]
    as_of = max(date.today(), max((dt.date() for dt in dates if dt), default=date.today()))
    order_stats = summarize_orders(orders, as_of)
    items = []

    for inventory_item in inventory_items:
        sku = clean_sku(inventory_item.get("sku"))
        if not sku:
            continue

        variant = inventory_item.get("variant") or {}
        product = variant.get("product") or {}
        stats = order_stats.get(sku, {})
        levels = (inventory_item.get("inventoryLevels") or {}).get("nodes") or []
        stock_now = sum(quantity_map(level).get("on_hand", 0) for level in levels)
        available = sum(quantity_map(level).get("available", 0) for level in levels)
        committed = sum(quantity_map(level).get("committed", 0) for level in levels)
        incoming = sum(quantity_map(level).get("incoming", 0) for level in levels)
        unavailable = sum(quantity_map(level).get("unavailable", 0) for level in levels)

        sales7 = stats.get("quantity7", 0)
        sales30 = stats.get("quantity30", 0)
        sales90 = stats.get("quantity90", 0)
        average_sales_price = average(stats.get("prices", []))
        cost_price = to_float((inventory_item.get("unitCost") or {}).get("amount"))

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
            "product": product.get("title") or variant.get("title") or sku,
            "specs": option_specs(variant.get("selectedOptions")),
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
            "available": available,
            "committed": committed,
            "incoming": incoming,
            "unavailable": unavailable,
            "weeksUntilOut": weeks_until_out,
            "orderLineCounts": {
                "sales7": stats.get("lineCount7", 0),
                "sales30": stats.get("lineCount30", 0),
                "sales90": stats.get("lineCount90", 0),
            },
        })

    return items, as_of


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def main():
    client = ShopifyClient()
    inventory_items = fetch_all_inventory(client)
    orders = fetch_recent_orders(client)
    items, as_of = build_items(inventory_items, orders)

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    write_json(RAW_DIR / f"inventory-items-{stamp}.json", inventory_items)
    write_json(RAW_DIR / f"orders-{stamp}.json", orders)

    payload = {
        "generatedFrom": "Shopify Admin GraphQL API",
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "asOfDate": as_of.isoformat(),
        "counts": {
            "dataItems": len(items),
            "inventoryItems": len(inventory_items),
            "orders": len(orders),
        },
        "calculationNotes": {
            "salesCounts": "Uses line item quantity summed by SKU over 7/30/90 days.",
            "averageSalesPrice": "Simple average of order line original unit price by SKU.",
            "stockNow": "Sums inventory level on_hand quantities across locations.",
            "netMargin": "Uses average sales price multiplied by 0.8 before subtracting cost price.",
            "segment": "KIDS if SKU ends 02, 04, 06, or 08. All other SKUs are WOMENS.",
        },
        "items": items,
        "reports": [],
    }
    write_json(OUTPUT, payload)

    kids = sum(1 for item in items if item["segment"] == "KIDS")
    print(json.dumps({
        "items": len(items),
        "orders": len(orders),
        "asOfDate": payload["asOfDate"],
        "kids": kids,
        "womens": len(items) - kids,
        "output": str(OUTPUT),
    }, indent=2))


if __name__ == "__main__":
    main()
