"""
Linkcheck, the real contract file executed against the GenVM simulator.

    pytest tests -v

The leader and the validator get their own independent mock web, so a leader
that lies and a validator that sees a different page are both testable.
"""

import pytest

import glsim as S

PATH = "linkcheck.py"
M = S.load_contract(PATH)

PAGE = "https://example.org/resources"
DEAD = "https://old.example.net/guide"
ALIVE = "https://docs.example.net/start"
OWNER = "0x" + "11" * 20
ANN = "0x" + "22" * 20
BEN = "0x" + "33" * 20
REWARD = 1000
W = 3600
T0 = 1_790_000_000

HTML = (f'<html><a href="{DEAD}">guide</a> <a href="{ALIVE}">start</a> '
        f'<a href="https://x.example.com/a?b=1&amp;c=2">q</a></html>').encode()


def at(offset):
    S.set_time(M.iso(T0 + offset))


def web(page=HTML, **links):
    pages = {PAGE: {"status": 200, "body": page}}
    for url, status in links.items():
        pages[url] = {"status": status, "body": b""}
    return pages


def world(dead=404, alive=200, page=HTML):
    return {PAGE: {"status": 200, "body": page}, DEAD: {"status": dead, "body": b""}, ALIVE: {"status": alive, "body": b""}}


class Base:
    def setup_method(self):
        S.reset_transfers()
        S.set_mocks()
        at(0)

    def deploy(self, value=REWARD, window=W, page=PAGE):
        c = S.deploy(PATH)
        S.set_sender(OWNER)
        S.call(c, "open", page, window, value=value)
        return c

    def claim(self, c, link, who=ANN, pages=None, v_pages=None, bid=0):
        S.set_mocks(leader_pages=pages if pages is not None else world(), validator_pages=v_pages)
        S.set_sender(who)
        try:
            return S.call(c, "claim", bid, link)
        finally:
            S.set_sender(OWNER)

    def settle(self, c, who=BEN, bid=0):
        S.set_sender(who)
        try:
            return S.call(c, "settle", bid)
        finally:
            S.set_sender(OWNER)


class TestPure:
    def test_key_of_ignores_case_of_host_fragment_and_trailing_slash(self):
        assert M.key_of("HTTPS://Old.Example.NET/guide/#top") == M.key_of("https://old.example.net/guide")

    def test_listed_in_accepts_escaped_ampersands(self):
        assert M.listed_in('<a href="https://x.com/a?b=1&amp;c=2">', "https://x.com/a?b=1&c=2")
        assert not M.listed_in("<p>nothing</p>", "https://x.com/a")
        assert not M.listed_in("anything", "")

    @pytest.mark.parametrize("code,dead", [("404", True), ("410", True), ("200", False), ("403", False),
                                           ("500", False), ("301", False), ("000", False)])
    def test_only_404_and_410_are_dead(self, code, dead):
        assert M.is_dead(code) is dead

    def test_is_http_url(self):
        assert M.is_http_url("https://a.example/x") and M.is_http_url("http://a.example")
        assert not M.is_http_url("ftp://a.example") and not M.is_http_url("javascript:alert(1)")
        assert not M.is_http_url("https://" + "a" * 400 + ".com")

    def test_agreement_is_about_listed_and_dead_not_the_exact_code(self):
        a = {"listed": "yes", "status": "404"}
        assert M.linkcheck_agrees(a, {"listed": "yes", "status": "410"})
        assert not M.linkcheck_agrees(a, {"listed": "yes", "status": "200"})
        assert not M.linkcheck_agrees(a, {"listed": "no", "status": "000"})
        assert not M.linkcheck_agrees(a, {"listed": "yes", "status": "40"})
        assert not M.linkcheck_agrees(a, "yes")


class TestOpen(Base):
    def test_a_bounty_opens_with_the_reward_held(self):
        c = self.deploy()
        b = c.bounty(0)
        assert (b["page"], b["owner"], b["reward"], b["status"]) == (PAGE, OWNER, str(REWARD), "open")
        assert b["closes_at"] == M.iso(T0 + W) and int(c.held_total()) == REWARD

    @pytest.mark.parametrize("page,window,value", [
        ("ftp://a.example/x", W, REWARD), ("not a url", W, REWARD), (PAGE, 5, REWARD), (PAGE, W, 0)])
    def test_bad_opens_are_refused(self, page, window, value):
        c = S.deploy(PATH)
        S.set_sender(OWNER)
        with pytest.raises(Exception):
            S.call(c, "open", page, window, value=value)


class TestClaim(Base):
    def test_a_dead_link_the_page_lists_pays_the_reward(self):
        c = self.deploy()
        self.claim(c, DEAD)
        assert S.transfers() == [(ANN, REWARD)]
        b = c.bounty(0)
        assert b["status"] == "paid" and b["winner"] == ANN and b["dead_link"] == DEAD
        assert int(c.held_total()) == 0
        assert c.claims_of(0)["claims"][0]["status"] == "404"

    def test_a_410_is_dead_too(self):
        c = self.deploy()
        self.claim(c, DEAD, pages=world(dead=410))
        assert S.transfers() == [(ANN, REWARD)]

    def test_a_live_link_is_recorded_and_pays_nobody(self):
        c = self.deploy()
        self.claim(c, ALIVE)
        assert S.transfers() == [] and c.bounty(0)["status"] == "open"
        assert c.claims_of(0)["claims"][0]["dead"] == "no"

    @pytest.mark.parametrize("status", [403, 500, 503, 301, 429])
    def test_an_unreliable_server_never_costs_the_owner(self, status):
        c = self.deploy()
        self.claim(c, DEAD, pages=world(dead=status))
        assert S.transfers() == []

    def test_a_link_the_page_does_not_list_is_not_fetched_and_not_paid(self):
        c = self.deploy()
        ghost = "https://gone.example.org/never-written"
        pages = world()
        pages[ghost] = {"status": 404, "body": b""}
        self.claim(c, ghost, pages=pages)
        assert S.transfers() == []
        row = c.claims_of(0)["claims"][0]
        assert (row["listed"], row["dead"]) == ("no", "no")

    def test_the_link_is_never_fetched_when_it_is_not_on_the_page(self):
        c = self.deploy()
        ghost = "https://gone.example.org/never-written"
        S.set_mocks(leader_pages={**world(), ghost: {"status": 404, "body": b""}})
        S.set_sender(ANN)
        S.call(c, "claim", 0, ghost)
        assert ghost not in S.RT.leader_env.web_calls

    def test_the_page_must_answer_200(self):
        c = self.deploy()
        pages = world()
        pages[PAGE] = {"status": 500, "body": b""}
        with pytest.raises(Exception):
            self.claim(c, DEAD, pages=pages)
        assert c.bounty(0)["claims"] == 0

    def test_a_link_with_an_escaped_ampersand_is_found(self):
        c = self.deploy()
        link = "https://x.example.com/a?b=1&c=2"
        pages = world()
        pages[link] = {"status": 404, "body": b""}
        self.claim(c, link, pages=pages)
        assert S.transfers() == [(ANN, REWARD)]

    def test_a_dead_link_is_paid_once_then_the_bounty_is_closed(self):
        c = self.deploy()
        self.claim(c, DEAD)
        with pytest.raises(Exception, match="paid"):
            self.claim(c, ALIVE, who=BEN)
        assert S.transfers() == [(ANN, REWARD)]


class TestFreeRefusals(Base):
    def test_the_owner_cannot_claim(self):
        c = self.deploy()
        with pytest.raises(Exception, match="owner"):
            self.claim(c, DEAD, who=OWNER)

    def test_the_same_link_is_tried_once_in_any_spelling(self):
        c = self.deploy()
        self.claim(c, ALIVE)
        with pytest.raises(Exception, match="already tried"):
            self.claim(c, "HTTPS://Docs.Example.NET/start/")
        with pytest.raises(Exception, match="already tried"):
            self.claim(c, ALIVE + "#top")

    def test_the_page_itself_is_not_a_link_to_it(self):
        c = self.deploy()
        with pytest.raises(Exception, match="page itself"):
            self.claim(c, PAGE + "/")

    def test_a_claim_after_the_deadline_is_refused_before_any_fetch(self):
        c = self.deploy()
        at(W + 1)
        S.set_mocks(leader_pages=world())
        S.set_sender(ANN)
        with pytest.raises(Exception, match="closed"):
            S.call(c, "claim", 0, DEAD)
        assert S.RT.leader_env.web_calls == []

    def test_a_bad_link_is_refused_before_any_fetch(self):
        c = self.deploy()
        S.set_mocks(leader_pages=world())
        S.set_sender(ANN)
        with pytest.raises(Exception, match="http"):
            S.call(c, "claim", 0, "file:///etc/passwd")
        assert S.RT.leader_env.web_calls == []

    def test_no_such_bounty(self):
        c = self.deploy()
        with pytest.raises(Exception, match="no such"):
            self.claim(c, DEAD, bid=7)


class TestValidator(Base):
    def test_a_leader_that_claims_a_dead_link_the_validator_sees_alive_is_rejected(self):
        c = self.deploy()
        with pytest.raises(Exception, match="did not agree"):
            self.claim(c, DEAD, pages=world(dead=404), v_pages=world(dead=200))
        assert S.transfers() == [] and c.bounty(0)["claims"] == 0

    def test_a_leader_that_says_listed_when_the_validator_sees_no_such_link_is_rejected(self):
        c = self.deploy()
        with pytest.raises(Exception, match="did not agree"):
            self.claim(c, DEAD, pages=world(), v_pages=world(page=b"<html>no links</html>"))

    def test_a_forged_payload_is_rejected(self):
        c = self.deploy()
        S.set_mocks(leader_pages=world(dead=200))
        S.set_leader_payload({"listed": "yes", "status": "404"})
        S.set_sender(ANN)
        with pytest.raises(Exception, match="did not agree"):
            S.call(c, "claim", 0, DEAD)
        assert S.transfers() == []

    @pytest.mark.parametrize("payload", [{"listed": "maybe", "status": "404"}, {"listed": "yes", "status": "dead"},
                                         {"listed": "yes"}, {"listed": "yes", "status": "40"}])
    def test_an_unsound_payload_never_pays(self, payload):
        c = self.deploy()
        S.set_mocks(leader_pages=world())
        S.set_leader_payload(payload)
        S.set_sender(ANN)
        with pytest.raises(Exception):
            S.call(c, "claim", 0, DEAD)
        assert S.transfers() == []

    def test_no_model_is_ever_asked(self):
        c = self.deploy()
        self.claim(c, DEAD)
        assert S.RT.leader_env.prompt_calls == [] and S.RT.validator_env.prompt_calls == []

    def test_a_link_that_cannot_be_fetched_is_not_dead(self):
        c = self.deploy()
        pages = {PAGE: {"status": 200, "body": HTML}}          # the link has no mock: the fetch raises
        self.claim(c, DEAD, pages=pages)
        assert S.transfers() == []
        assert c.claims_of(0)["claims"][0]["status"] == "000"


class TestSettle(Base):
    def test_an_unclaimed_bounty_goes_back_to_the_owner_after_the_deadline(self):
        c = self.deploy()
        self.claim(c, ALIVE)
        at(W + 1)
        self.settle(c)
        assert S.transfers() == [(OWNER, REWARD)]
        assert c.bounty(0)["status"] == "refunded" and int(c.held_total()) == 0

    def test_settle_before_the_deadline_is_refused(self):
        c = self.deploy()
        with pytest.raises(Exception, match="open until"):
            self.settle(c)

    def test_a_paid_bounty_cannot_be_settled_again(self):
        c = self.deploy()
        self.claim(c, DEAD)
        at(W + 1)
        with pytest.raises(Exception, match="paid"):
            self.settle(c)
        assert S.transfers() == [(ANN, REWARD)]

    def test_a_refunded_bounty_cannot_be_settled_twice(self):
        c = self.deploy()
        at(W + 1)
        self.settle(c)
        with pytest.raises(Exception, match="refunded"):
            self.settle(c)
        assert S.transfers() == [(OWNER, REWARD)]


class TestMoney(Base):
    def test_two_bounties_never_pay_from_each_other(self):
        c = self.deploy()
        S.set_sender(OWNER)
        S.call(c, "open", PAGE, W, value=500)
        self.claim(c, DEAD, bid=1)
        assert S.transfers() == [(ANN, 500)] and int(c.held_total()) == REWARD
