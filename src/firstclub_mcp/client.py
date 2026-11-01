"""Thin async client for the FirstClub app API (reverse-engineered from app v2.1.57)."""
from __future__ import annotations

import os
import re
import time
import secrets
from pathlib import Path
from typing import Any

import httpx

from .session import Session

BASE_URL = "https://prod-ultra.firstclub.tech"
MAPS_PROXY_URL = "https://argus-api.firstclub.tech/v3/maps/api"  # FirstClub-hosted proxy, not Google directly
APP_VERSION_CODE = "264457"   # App-Version header (build number)
APP_VERSION_NAME = "2.1.57"
USER_AGENT = "firstclub-mcp/0.1"

# Server-driven page/layout ids hard-coded in the app.
PAGE_HOME_GUEST = "76154e90-5fc6-4c91-af25-fb35bb706cef"
LAYOUT_SEARCH = "1c90eba5-eb9b-4aeb-89cc-4acb72990768"
LAYOUT_PDP = "aef046b1-2872-4ffe-9140-a74fb5ee0c1f"
LAYOUT_CLP_V2 = "c246f027-a8ab-414a-8f05-0e058abec930"
PAGE_CART = "79040524-2bb2-4ccf-944e-52ef4b005879"  # app fallback; the live id comes from /cart-page/config
DEFAULT_GUEST_ADDRESS = "LOCID25052000167"
FALLBACK_WAREHOUSE = "FCHBLRSJR01"



def _guest_id() -> str | None:
    """The app's shared guest user id (built into the app). Not a secret; kept out of git anyway.
    Optional: only the logged-out home page (list_categories) needs it."""
    if v := os.environ.get("FIRSTCLUB_GUEST_ID"):
        return v
    p = Path(__file__).resolve().parents[2] / ".guest_id"
    return p.read_text().strip() if p.exists() else None


_SAFE_SEGMENT = re.compile(r"[A-Za-z0-9_-]{1,80}")


def seg(value: Any) -> str:
    """Validate an id before putting it in a URL path (blocks ../, ?, # and friends)."""
    v = str(value or "")
    if not _SAFE_SEGMENT.fullmatch(v):
        raise FirstClubError(0, f"invalid id: {v[:40]!r}")
    return v


class FirstClubError(Exception):
    def __init__(self, status: int, message: str, payload: Any = None):
        super().__init__(f"HTTP {status}: {message}")
        self.status, self.message, self.payload = status, message, payload


class NeedsLogin(FirstClubError):
    pass


class NeedsLocation(FirstClubError):
    pass


class FirstClub:
    def __init__(self, session: Session | None = None):
        self.s = session or Session.load()
        self.http = httpx.AsyncClient(base_url=BASE_URL, timeout=30, headers={
            "Content-Type": "application/json", "Accept": "application/json",
            "Source": "demand_app", "App-Version": APP_VERSION_CODE, "User-Agent": USER_AGENT,
        })

    async def aclose(self):
        await self.http.aclose()

    # ---------- plumbing ----------
    def _auth_headers(self) -> dict[str, str]:
        if self.s.logged_in:
            h = {"X-Session-Id": self.s.session_id, "x-userId": self.s.user_id}
            if self.s.account_user_id:
                h["X-User-Id"] = self.s.account_user_id
            return h
        g = _guest_id()
        return {"X-User-Id": g, "X-Userid": g, "x-userId": g} if g else {}

    def _device(self) -> dict:
        return {"id": self.s.device_id, "type": "ANDROID", "os_version": "34",
                "app_version": APP_VERSION_NAME, "ip_address": "", "location": ""}

    async def request(self, method: str, path: str, *, json: Any = None, params: dict | None = None,
                      retry: bool = True, _retried: bool = False) -> Any:
        """retry=False for calls that must never be sent twice (checkout, cancel)."""
        c = self.http
        r = await c.request(method, path, json=json, params=params, headers=self._auth_headers())
        if (r.status_code == 406 and retry and self.s.logged_in and not _retried
                and not path.startswith("/api/v1/session-service/")):
            await self.refresh_session()
            return await self.request(method, path, json=json, params=params, retry=retry, _retried=True)
        try:
            data = r.json()
        except ValueError:
            data = {"raw": r.text[:500]}
        if r.status_code in (401, 406, 407):
            msg = (data.get("error") or {}).get("message") if isinstance(data, dict) else None
            if self.s.logged_in and r.status_code == 407:
                self.s.clear_auth()  # only an explicit auth rejection ends the session; 408/409 are ordinary errors
            raise NeedsLogin(r.status_code, msg or "session required; log in", data)
        if r.status_code >= 400 or (isinstance(data, dict) and data.get("success") is False):
            err = data.get("error") if isinstance(data, dict) else None
            msg = (err or {}).get("message") if isinstance(err, dict) else (data.get("message") if isinstance(data, dict) else None)
            raise FirstClubError(r.status_code, msg or "request failed", data)
        return data

    async def body(self, method: str, path: str, **kw) -> Any:
        d = await self.request(method, path, **kw)
        return d.get("body", d) if isinstance(d, dict) else d

    def require_location(self) -> None:
        if self.s.logged_in and not (self.s.address_id and self.s.store_id):
            raise NeedsLocation(0, "no delivery address selected; call set_location so the user can pick a saved address")

    def user_context(self, with_location: bool = True) -> dict:
        uid = self.s.user_id if self.s.logged_in else _guest_id()
        if self.s.logged_in:
            self.require_location()
        ctx: dict = {"userId": uid, "address": {"addressId": self.s.address_id or DEFAULT_GUEST_ADDRESS}}
        if with_location and self.s.lat is not None:
            ctx["location"] = {"lat": self.s.lat, "long": self.s.lng}
        return ctx

    @property
    def warehouse_id(self) -> str:
        self.require_location()
        return self.s.store_id or FALLBACK_WAREHOUSE  # fallback only reachable when logged out

    # ---------- auth ----------
    async def send_otp(self, phone: str) -> dict:
        phone = phone.strip().replace(" ", "")
        b = await self.body("POST", "/api/v1/page/login/send-otp", json={
            "channel": phone, "channel_type": "SMS", "device": self._device(),
            "pageInstanceId": f"{int(time.time()*1000)}-{secrets.token_hex(5)[:9]}",
            "deviceId": self.s.device_id})
        otp_id = (b or {}).get("otpId")
        if not otp_id:
            raise FirstClubError(200, "send-otp returned no otpId", b)
        self.s.phone, self.s.pending_otp_id = phone, otp_id
        self.s.save()
        return {"otp_sent_to": phone, "otpId": otp_id}

    async def verify_otp(self, code: str) -> dict:
        if not (self.s.pending_otp_id and self.s.phone):
            raise FirstClubError(0, "no pending OTP; call send_otp first")
        code = re.sub(r"\s", "", code or "")
        if not re.fullmatch(r"\d{4,6}", code):
            raise FirstClubError(0, "the OTP should be the 4-6 digit code from the SMS")
        phone = self.s.phone if self.s.phone.startswith("+") else "+" + self.s.phone
        b = await self.body("POST", "/api/v1/page/login/validate-otp", params={"blacklistValidation": "true"}, json={
            "otp_id": self.s.pending_otp_id, "otp_code": code.strip(), "channel": phone,
            "channel_type": "SMS", "device": self._device(), "deviceId": self.s.device_id})
        acct = b.get("userAccountInformation") or {}
        self.s.session_id = (b.get("session_info") or {}).get("sessionId")
        self.s.user_id = b.get("user_id") or b.get("userId")
        self.s.account_user_id = acct.get("userId")
        self.s.account_id = acct.get("accountId")
        self.s.pending_otp_id = None
        self.s.save()
        if not self.s.logged_in:
            raise FirstClubError(200, "validate-otp succeeded but no session/user id in response", list(b))
        return {"logged_in": True, "isNewUser": b.get("isNewUser", b.get("is_new_user")), "tier": b.get("tier")}

    async def refresh_session(self) -> None:
        r = await self.http.post("/api/v1/session-service/refresh-session", json={"device": self._device()},
                                 headers={"X-Session-Id": self.s.session_id or "", "X-User-Id": self.s.account_user_id or ""})
        sid = None
        try:
            sid = (r.json().get("body") or {}).get("sessionId")
        except ValueError:
            pass
        if not sid:
            # Only an explicit rejection means the session is dead; anything else keeps it.
            if r.status_code in (401, 406, 407, 408, 409):
                self.s.clear_auth()
                raise NeedsLogin(r.status_code, "session expired and refresh failed; log in again")
            raise FirstClubError(r.status_code, "refresh-session returned no sessionId", r.text[:300])
        self.s.session_id = sid
        self.s.save()

    async def logout(self) -> None:
        try:
            await self.request("POST", "/api/v1/session-service/logout", json={
                "session_id": self.s.session_id, "userId": self.s.account_id,
                "chanel_info": {"type": "SMS", "identifier": self.s.phone}, "device": self._device()})
        finally:
            self.s.clear_auth()

    # ---------- location ----------
    async def addresses(self) -> list[dict]:
        b = await self.body("GET", f"/api/v1/page/user-profile/address/{seg(self.s.user_id)}")
        return (b or {}).get("addresses", [])

    async def serviceability(self, lat: float, lng: float) -> dict:
        return await self.body("POST", "/api/v1/page/user-profile/serviceability/v3/flattened",
                               params={"userId": self.s.user_id, "version": "v4"},
                               json={"geometry": {"latitude": lat, "longitude": lng}, "userId": self.s.user_id})

    async def maps(self, path: str, params: dict) -> dict:
        """FirstClub's own Google-Maps proxy (the app's geocoding path); authenticated by the app session."""
        async with httpx.AsyncClient(base_url=MAPS_PROXY_URL, timeout=20,
                                     headers={"User-Agent": USER_AGENT, "Accept": "application/json"}) as h:
            r = await h.get(path, params=params, headers={"X-Session-Id": self.s.session_id or "",
                                                          "X-User-Id": self.s.account_user_id or ""})
        if r.status_code in (401, 403):
            raise NeedsLogin(r.status_code, "maps lookup needs a logged-in session")
        r.raise_for_status()
        return r.json()

    async def add_address(self, address: dict) -> Any:
        return await self.body("POST", "/api/v1/page/user-profile/address",
                               json={"userId": self.s.user_id, "address": address})

    # ---------- catalog ----------
    async def home_guest(self, address_id: str | None = None) -> dict:
        g = _guest_id()
        if not g:
            raise FirstClubError(0, "logged-out browsing needs FIRSTCLUB_GUEST_ID; log in instead")
        return await self.body("POST", "/api/v1/serving-orchestrator/non-logged-in/home/widget",
                               params={"apiVersion": "v2"},
                               json={"pageId": PAGE_HOME_GUEST,
                                     "userContext": {"userId": g, "address": {"addressId": address_id or DEFAULT_GUEST_ADDRESS}}})

    async def search(self, query: str, vertical: str = "GROCERY") -> dict:
        ctx = self.user_context()
        wh = (self.s.cafe_store_id or self.warehouse_id) if vertical == "CAFE" else self.warehouse_id
        ctx.update(accountId=self.s.account_id, warehouseId=wh)
        return await self.request("POST", "/api/v5/search-products", json={
            "userContext": ctx, "query": query, "layoutId": LAYOUT_SEARCH, "enableOOS": True,
            "verticalType": vertical, "purchaseType": "ONCE"})

    async def category(self, category_id: str, page: int = 1) -> dict:
        return await self.body("POST", "/api/v2/serving-orchestrator/category-listing/browse",
                               params={"apiVersion": "v2"},
                               json={"userContext": self.user_context(), "pageNumber": page,
                                     "categoryId": category_id, "layoutId": LAYOUT_CLP_V2,
                                     "refinements": {"quickFilterIds": []}})

    async def products(self, fsns: list[str]) -> dict:
        return await self.body("POST", "/api/v1/serving-orchestrator/products", json={
            "userContext": self.user_context(), "fsnList": fsns, "layoutId": LAYOUT_PDP})

    async def variants(self, fsn: str) -> list:
        return await self.body("POST", "/api/v1/product-family/bulk-fetch", json={
            "addressId": self.user_context()["address"]["addressId"], "accountId": self.s.user_id,
            "vertical": "GROCERY", "purchaseType": "ONCE",
            "products": [{"productId": fsn, "familyTypes": ["variation", "laddering", "bundle"]}]})

    # ---------- cart / orders ----------
    async def cart(self) -> dict:
        return await self.body("GET", f"/api/v2/cart-page/{seg(self.s.user_id)}/painted", params={
            "addressId": self.user_context()["address"]["addressId"], "pageId": await self.cart_page(),
            "strategyType": "EXACT", "refreshRules": "true"})

    @staticmethod
    def cart_items(painted: dict) -> list[dict]:
        def walk(o):
            if isinstance(o, dict):
                yield o
                for v in o.values():
                    yield from walk(v)
            elif isinstance(o, list):
                for v in o:
                    yield from walk(v)
        for d in walk(painted):
            if isinstance(d.get("cartDto"), dict):
                return d["cartDto"].get("items") or []
            if isinstance(d.get("activeCart"), dict):
                return d["activeCart"].get("items") or []
        return []

    async def cart_set(self, items: list[dict]) -> dict:
        """items: [{fsn, listingId, quantity}] — absolute quantities (0 removes).
        Uses the live v2 cart-page bulk-action (deltas); the older bulk-update endpoint is a silent no-op."""
        wanted: dict[tuple[str, str], int] = {}
        for i in items:
            q = i.get("quantity")
            if not isinstance(q, int) or isinstance(q, bool) or not 0 <= q <= 50:
                raise FirstClubError(0, f"quantity must be a whole number 0-50 (got {q!r})")
            if not i.get("fsn") or not i.get("listingId"):
                raise FirstClubError(0, "each item needs fsn and listingId")
            key = (str(i["fsn"]), str(i["listingId"]))
            if key in wanted:
                raise FirstClubError(0, f"{key[0]} is listed twice; give one absolute quantity per item")
            wanted[key] = q
        current = {(i.get("fsnId"), i.get("listingId")): int(i.get("quantity") or 0)
                   for i in self.cart_items(await self.cart()) if not i.get("isFreebie")}  # offer lines are separate
        actions = []
        for (fsn, listing), q in wanted.items():
            i = {"fsn": fsn, "listingId": listing, "quantity": q}
            delta = q - current.get((fsn, listing), 0)
            if delta:
                actions.append({"itemIds": {"fsnId": i["fsn"], "listingId": i["listingId"]},
                                "actionType": "INCREMENT" if delta > 0 else "DECREMENT", "quantity": abs(delta)})
        if not actions:
            return {"unchanged": True}
        return await self.body("POST", "/api/v2/cart-page/bulk-action", json={
            "userId": self.s.user_id, "addressId": self.user_context()["address"]["addressId"],
            "itemActions": actions, "deviceId": self.s.device_id, "strategyType": "EXACT"})

    async def orders(self, page: int = 0, size: int = 10) -> dict:
        return await self.body("GET", "/api/v1/page/user-profile/order", params={
            "userId": self.s.user_id, "page": page, "size": size, "sortBy": "createdAt", "sortDirection": "DESC"})

    async def order(self, order_id: str) -> dict:
        return await self.body("GET", f"/api/v1/page/user-profile/order/page/{seg(self.s.user_id)}/{seg(order_id)}")

    async def buy_again(self) -> dict:
        return await self.body("POST", "/api/v1/reorder/products/user",
                               json={"addressId": self.user_context()["address"]["addressId"]})

    async def cart_page(self) -> str:
        """The cart page id the live app uses (server config; it carries the free-gift/deal 'zones')."""
        if not getattr(self, "_cart_page", None):
            try:
                self._cart_page = (await self.body("GET", "/api/v2/cart-page/config")).get("cartPageId") or PAGE_CART
            except FirstClubError:
                self._cart_page = PAGE_CART
        return self._cart_page

    async def update_offer_item(self, rule: dict, action: str) -> dict:
        """Add (INCREMENT) or remove (DECREMENT) the item of a free-gift / deal rule, as the app does."""
        pl = rule.get("productListing") or {}
        return await self.body("POST", "/api/v2/cart-page/freebie/update/v2", json={
            "userId": self.s.user_id, "addressId": self.user_context()["address"]["addressId"],
            "action": action, "itemId": {"fsnId": pl.get("fsn"), "listingId": pl.get("listingId")},
            "isFreebie": True, "reward": {"ruleId": rule.get("ruleId"), "additionalAttributes": {"ruleType": rule.get("ruleType")}},
            "refreshRules": True, "pageId": await self.cart_page(), "deviceId": self.s.device_id, "strategyType": "EXACT"})

    # ---------- coupons (apply to this device's cart) ----------
    def _cart_ids(self) -> dict:
        return {"userId": self.s.user_id, "addressId": self.user_context()["address"]["addressId"],
                "deviceId": self.s.device_id}

    async def coupons(self) -> dict:
        return await self.body("POST", "/api/v2/cart-page/coupons-new", json={**self._cart_ids(), "pageId": await self.cart_page()})

    async def apply_coupon(self, code: str, coupon_id: str) -> Any:
        return await self.request("POST", "/api/v2/cart-page/coupons-new/apply",
                                  json={**self._cart_ids(), "couponCode": code, "couponId": coupon_id})

    async def remove_coupon(self) -> Any:
        return await self.request("POST", "/api/v2/cart-page/coupons-new/remove", json={**self._cart_ids(), "pageId": await self.cart_page()})

    # ---------- money / tracking ----------
    async def wallet(self) -> dict:
        return await self.body("GET", "/api/v2/wallets/balance", params={"userId": self.s.user_id})

    async def wallet_ledger(self, wallet_id: str, page: int = 0, size: int = 20) -> dict:
        return await self.body("GET", f"/api/v1/wallets/{seg(wallet_id)}/ledger", params={"page": page, "size": size})

    async def cashback(self) -> dict:
        return await self.body("GET", "/api/v1/cashback/user/ledger", params={"userId": self.s.user_id})

    async def track_status(self, order_id: str) -> dict:
        return await self.body("GET", f"/api/v1/serving-orchestrator/order-tracking/{seg(order_id)}/status")

    async def track_details(self, order_id: str) -> dict:
        return await self.body("GET", f"/api/v1/serving-orchestrator/order-tracking/{seg(order_id)}/details")

    # ---------- checkout (wallet-only; not yet exercised against the live API) ----------
    async def validate_checkout(self) -> dict:
        return await self.body("GET", f"/api/v2/cart-page/validate-checkout/{seg(self.s.user_id)}/painted", params={
            "deviceId": self.s.device_id, "addressId": self.user_context()["address"]["addressId"],
            "pageId": await self.cart_page(), "strategyType": "EXACT"})

    async def initiate_checkout(self, cart_version: Any, payment_infos: list[dict]) -> dict:
        # Raw request: the caller needs success/error codes (e.g. 500 "Amount mismatch"), not just body.
        return await self.request("POST", "/api/v2/cart-page/checkout", retry=False, json={
            "userId": self.s.user_id, "addressId": self.user_context()["address"]["addressId"],
            "cartVersion": cart_version,
            "metaData": {"appsflyer_id": "", "os": "android", "app_instance_id": "", "idfv": ""},
            "paymentInfos": payment_infos, "deviceId": self.s.device_id, "strategyType": "EXACT"})

    async def checkout_state(self, checkout_id: str) -> dict:
        return await self.body("GET", f"/api/v1/checkout/{seg(checkout_id)}")

    async def cancel_order(self, order_id: str) -> Any:
        return await self.request("POST", "/api/v1/change-order/cancel", retry=False, json={
            "orderId": order_id, "requestedBy": "CUSTOMER", "type": "FULL",
            "cancellationReason": {"id": "customer_requested"}})

    async def release_checkout(self, checkout_id: str) -> Any:
        return await self.request("POST", f"/api/v1/checkout/release/{seg(checkout_id)}", retry=False)

    # ---------- dietary profile ----------
    async def profile_questions(self) -> Any:
        return await self.body("GET", "/api/v1/user-service/profile/questions", params={"userId": self.s.user_id})

    async def preferences(self) -> Any:
        return await self.body("GET", "/api/v1/user-service/preferences/user-preferences", params={"userId": self.s.user_id})

    # ---------- wishlist (per account; visible in the app under Profile -> Wishlist) ----------
    async def wishlist(self) -> dict:
        return await self.body("POST", "/api/v1/wishlist/shelf",
                               json={"userContext": self.user_context(), "warehouseId": self.warehouse_id})

    async def create_collection(self, name: str) -> dict:
        return await self.body("POST", "/api/v1/wishlist/collections", json={"userId": self.s.user_id, "name": name})

    async def save_item(self, fsn: str) -> dict:
        return await self.body("POST", "/api/v1/wishlist/items", json={"userId": self.s.user_id, "productId": fsn})

    async def unsave_item(self, fsn: str) -> dict:
        return await self.body("DELETE", "/api/v1/wishlist/items", json={"userId": self.s.user_id, "productId": fsn})

    async def add_to_collections(self, fsn: str, collection_ids: list[int]) -> dict:
        return await self.body("POST", "/api/v1/wishlist/collections/items",
                               json={"userId": self.s.user_id, "productId": fsn, "collectionIds": collection_ids})

    async def lab_reports(self, fsn: str) -> list[dict]:
        b = await self.body("GET", f"/api/v1/lab-reports/product/{seg(fsn)}")
        return sorted((b or {}).get("reports") or [], key=lambda r: r.get("rank") or 0)
