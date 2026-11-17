# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""
Linkcheck - a bounty for the first person who proves a link on a page is dead
=============================================================================

WHAT IT IS
    A site owner pays a reward in for one public page. Anyone who finds a
    link on that page that no longer works names it. The contract does not
    take their word, and it does not ask a model. In the same transaction,
    validators FETCH THE PAGE and check that the link really is written in
    it, then FETCH THE LINK and read the HTTP status the server answers with.
    A link the page lists whose server answers 404 or 410 is dead, and the
    reward is paid to whoever named it, at once.

THE PROBLEM IT SOLVES
    "There is a broken link on your site" is something a reader can claim
    and an owner can ignore. A bounty needs a check that nobody holding the
    money gets to make. Here the check is two public fetches, so the owner
    cannot wave a true report away and a reporter cannot be paid for a link
    the page never contained.

HOW CONSENSUS IS USED
    There is no judgment to make, so no model is asked anything. Each
    validator repeats both fetches itself and the answer must match exactly:

        listed   yes if the link is written in the page, else no
        status   404 or 410 -> dead; any other answer, or none -> not dead

    Only a clear 404 or 410 counts as dead. A timeout, a refusal to talk to a
    robot (403), a server error (5xx) or a redirect are all "not proven dead",
    so an unreliable server can never cost the owner a reward. Because the
    contract reads a status code, not a sentence, there is no prompt to
    inject: nothing the page says can change what it is worth.

    A block is only run for a claim that can still change anything. The
    contract first refuses, for free: a claim past the deadline, a claim on a
    settled bounty, the owner's own claim, a link already tried on this
    bounty, a link that is the page itself, and a link that is not an
    http(s) address.

WHO MAY WRITE
    open(page, window)      anyone, paying the reward in. The caller is the owner.
    claim(id, link)         anyone but the owner, until the deadline, once per link.
    settle(id)              anyone, once the deadline has passed; returns the reward
                            to the owner when nobody proved a link dead.

EVERY CLOCK
    closes_at               open + window. Claims are refused for free after it.
"""

from genlayer import *
from dataclasses import dataclass
import datetime


# ---------------------------------------------------------------------------
# Pure helpers, module level and unit tested from the contract file itself.
# ---------------------------------------------------------------------------

OPEN = "open"
PAID = "paid"
REFUNDED = "refunded"

ERR_EXPECTED = "[EXPECTED]"
ERR_FETCH = "[FETCH_ERROR]"

MAX_URL = 300
MAX_AMOUNT = 10 ** 27
MIN_WINDOW = 60
MAX_WINDOW = 365 * 86400
MAX_CLAIMS = 100                 # links tried per bounty
MAX_BODY = 400_000               # characters of a page that are read

DEAD_STATUSES = ("404", "410")

_EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)


def seconds(iso_text):
    text = str(iso_text).strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    delta = parsed - _EPOCH
    return delta.days * 86400 + delta.seconds


def iso(secs):
    return (_EPOCH + datetime.timedelta(seconds=int(secs))).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean_url(raw):
    """A URL as the caller typed it: one token, no whitespace, no control characters."""
    s = str(raw).strip()
    if any(ord(ch) < 33 or ord(ch) == 127 for ch in s):
        return ""
    return s


def is_http_url(url):
    low = url.lower()
    return (low.startswith("http://") or low.startswith("https://")) and len(url) <= MAX_URL and "." in url[8:]


def key_of(url):
    """What makes two spellings of one link the same try: case-insensitive scheme
    and host, no fragment, no trailing slash."""
    s = url.split("#", 1)[0]
    scheme_end = s.find("://")
    if scheme_end < 0:
        return s
    head = s[: scheme_end + 3].lower()
    rest = s[scheme_end + 3:]
    slash = rest.find("/")
    host, path = (rest, "") if slash < 0 else (rest[:slash], rest[slash:])
    return (head + host.lower() + path).rstrip("/")


def listed_in(body, link):
    """True if the link is written in the page. Compared as text, three ways a
    page can spell it: as given, with & escaped, or with no trailing slash."""
    if not link:
        return False
    variants = {link, link.replace("&", "&amp;"), link.rstrip("/")}
    return any(v and v in body for v in variants)


def status_text(code):
    try:
        n = int(code)
    except (TypeError, ValueError):
        return "0"
    return str(n) if 100 <= n <= 599 else "0"


def is_dead(status):
    return str(status) in DEAD_STATUSES


def verdict_sound(v):
    """Layer 1 of the validator. Free. The two fields have the shape the block makes."""
    return (isinstance(v, dict)
            and v.get("listed") in ("yes", "no")
            and isinstance(v.get("status"), str) and v.get("status").isdigit() and len(v.get("status")) == 3)


def linkcheck_agrees(mine, theirs):
    """Layer 2. Exact and symmetric over the two facts that decide payment:
    whether the link is listed, and whether its status is dead."""
    return (verdict_sound(mine) and verdict_sound(theirs)
            and mine["listed"] == theirs["listed"]
            and is_dead(mine["status"]) == is_dead(theirs["status"]))


def proves_dead(v):
    return v["listed"] == "yes" and is_dead(v["status"])


def fetch_status(url):
    """The HTTP status a URL answers with, as three digits, or 000 when the fetch fails."""
    try:
        resp = gl.nondet.web.get(url)
    except Exception:                                  # noqa: BLE001
        return "000"
    code = status_text(getattr(resp, "status", 0))
    return code.zfill(3) if code != "0" else "000"


def fetch_page(url):
    resp = gl.nondet.web.get(url)
    if status_text(getattr(resp, "status", 0)) != "200":
        raise gl.vm.UserError(f"{ERR_FETCH} the page did not answer 200")
    body = resp.body
    if body is None:
        raise gl.vm.UserError(f"{ERR_FETCH} the page was empty")
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="ignore")
    return str(body)[:MAX_BODY]


@gl.evm.contract_interface
class _Payee:
    class View:
        pass

    class Write:
        pass


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

@allow_storage
@dataclass
class Bounty:
    page: str
    owner: Address
    reward: u256
    status: str                # open | paid | refunded
    opened_at: u256
    closes_at: u256
    settled_at: u256
    claims: u256
    winner: Address
    dead_link: str
    first_claim: u256
    last_claim: u256


@allow_storage
@dataclass
class Claim:
    bounty: u256
    who: Address
    link: str
    listed: str                # yes | no
    status: str                # three digits, 000 when the fetch failed
    dead: str                  # yes | no
    at: str
    next: u256


class Linkcheck(gl.Contract):
    held: u256
    bounties: DynArray[Bounty]
    claims: DynArray[Claim]
    tried: TreeMap[str, u256]            # "bounty:linkkey" -> 1-based claim id

    def __init__(self):
        self.held = u256(0)

    # -- internal ---------------------------------------------------------

    def _now(self) -> int:
        return seconds(gl.message_raw["datetime"])

    def _bounty(self, bid: u256):
        i = int(bid)
        if i < 0 or i >= len(self.bounties):
            raise gl.vm.UserError(f"{ERR_EXPECTED} no such bounty")
        return self.bounties[i]

    def _send(self, to: Address, amount: int) -> None:
        if amount == 0:
            return
        if amount > int(self.held):
            raise gl.vm.UserError(f"{ERR_EXPECTED} payout exceeds what is held")
        self.held = u256(int(self.held) - amount)
        _Payee(to).emit_transfer(value=u256(amount))

    # -- writes -----------------------------------------------------------

    @gl.public.write.payable
    def open(self, page: str, window: u256) -> None:
        """Pay a reward in for one page. The reward and the clock are frozen here."""
        p = clean_url(page)
        if not is_http_url(p):
            raise gl.vm.UserError(f"{ERR_EXPECTED} the page must be an http(s) address of at most {MAX_URL} characters")
        w = int(window)
        if w < MIN_WINDOW or w > MAX_WINDOW:
            raise gl.vm.UserError(f"{ERR_EXPECTED} the window is {MIN_WINDOW} to {MAX_WINDOW} seconds")
        paid = int(gl.message.value)
        if paid < 1 or paid > MAX_AMOUNT:
            raise gl.vm.UserError(f"{ERR_EXPECTED} the reward is 1 to {MAX_AMOUNT} wei, not {paid}")
        now = self._now()
        self.bounties.append(Bounty(
            page=p, owner=gl.message.sender_address, reward=u256(paid), status=OPEN,
            opened_at=u256(now), closes_at=u256(now + w), settled_at=u256(0), claims=u256(0),
            winner=gl.message.sender_address, dead_link="", first_claim=u256(0), last_claim=u256(0),
        ))
        self.held = u256(int(self.held) + paid)

    @gl.public.write
    def claim(self, bounty_id: u256, link: str) -> None:
        """Name one link on the page. The validators fetch the page and the link;
        a link the page lists whose server answers 404 or 410 is paid at once."""
        b = self._bounty(bounty_id)
        bid = int(bounty_id)
        sender = gl.message.sender_address
        if str(b.status) != OPEN:
            raise gl.vm.UserError(f"{ERR_EXPECTED} this bounty is {b.status}; nothing is left to find")
        if self._now() > int(b.closes_at):
            raise gl.vm.UserError(f"{ERR_EXPECTED} claims closed at {iso(int(b.closes_at))}")
        if sender == b.owner:
            raise gl.vm.UserError(f"{ERR_EXPECTED} the owner cannot claim on their own bounty")
        url = clean_url(link)
        if not is_http_url(url):
            raise gl.vm.UserError(f"{ERR_EXPECTED} a link must be an http(s) address of at most {MAX_URL} characters")
        if key_of(url) == key_of(str(b.page)):
            raise gl.vm.UserError(f"{ERR_EXPECTED} the link is the page itself")
        key = f"{bid}:{key_of(url)}"
        if int(self.tried.get(key, u256(0))) > 0:
            raise gl.vm.UserError(f"{ERR_EXPECTED} this link was already tried on this bounty")
        if int(b.claims) >= MAX_CLAIMS:
            raise gl.vm.UserError(f"{ERR_EXPECTED} a bounty takes at most {MAX_CLAIMS} tried links")

        page = str(b.page)

        # ------------------------------------------------------------------
        # non-deterministic half: two fetches, no model, no storage.
        # ------------------------------------------------------------------
        def leader_fn():
            body = fetch_page(page)
            if not listed_in(body, url):
                return {"listed": "no", "status": "000"}
            return {"listed": "yes", "status": fetch_status(url)}

        def validator_fn(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                return False
            theirs = leaders_res.calldata
            if not verdict_sound(theirs):
                return False
            try:
                mine = leader_fn()
            except gl.vm.UserError:
                return False
            return linkcheck_agrees(mine, theirs)

        res = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        # ------------------------------------------------------------------
        # deterministic half
        # ------------------------------------------------------------------
        if not verdict_sound(res):
            raise gl.vm.UserError(f"{ERR_EXPECTED} the check returned no usable answer")
        dead = proves_dead(res)
        cid = len(self.claims)
        self.claims.append(Claim(
            bounty=u256(bid), who=sender, link=url, listed=str(res["listed"]), status=str(res["status"]),
            dead="yes" if dead else "no", at=gl.message_raw["datetime"], next=u256(0),
        ))
        if int(b.claims) == 0:
            b.first_claim = u256(cid)
        else:
            self.claims[int(b.last_claim)].next = u256(cid)
        b.last_claim = u256(cid)
        b.claims = u256(int(b.claims) + 1)
        self.tried[key] = u256(cid + 1)
        if dead:
            reward = int(b.reward)
            b.status = PAID
            b.winner = sender
            b.dead_link = url
            b.settled_at = u256(self._now())
            self._send(sender, reward)

    @gl.public.write
    def settle(self, bounty_id: u256) -> None:
        """Return the reward to the owner when nobody proved a link dead in time."""
        b = self._bounty(bounty_id)
        if str(b.status) != OPEN:
            raise gl.vm.UserError(f"{ERR_EXPECTED} this bounty is {b.status}; nothing to settle")
        now = self._now()
        if now <= int(b.closes_at):
            raise gl.vm.UserError(f"{ERR_EXPECTED} claims are open until {iso(int(b.closes_at))}")
        b.status = REFUNDED
        b.settled_at = u256(now)
        self._send(b.owner, int(b.reward))

    # -- reads ------------------------------------------------------------

    @gl.public.view
    def count(self) -> u256:
        return u256(len(self.bounties))

    @gl.public.view
    def held_total(self) -> u256:
        return u256(int(self.held))

    @gl.public.view
    def status(self, bounty_id: u256) -> str:
        return str(self._bounty(bounty_id).status)

    @gl.public.view
    def bounty(self, bounty_id: u256) -> dict:
        b = self._bounty(bounty_id)
        return {
            "page": str(b.page), "owner": str(b.owner), "reward": str(int(b.reward)), "status": str(b.status),
            "claims": int(b.claims), "winner": str(b.winner) if str(b.status) == PAID else "",
            "dead_link": str(b.dead_link), "opened_at": iso(int(b.opened_at)), "closes_at": iso(int(b.closes_at)),
            "settled_at": iso(int(b.settled_at)) if int(b.settled_at) > 0 else "",
        }

    @gl.public.view
    def claims_of(self, bounty_id: u256) -> dict:
        b = self._bounty(bounty_id)
        rows = []
        i = int(b.first_claim)
        for _ in range(int(b.claims)):
            c = self.claims[i]
            rows.append({"id": i, "who": str(c.who), "link": str(c.link), "listed": str(c.listed),
                         "status": str(c.status), "dead": str(c.dead), "at": str(c.at)})
            i = int(c.next)
        return {"page": str(b.page), "claims": rows}
