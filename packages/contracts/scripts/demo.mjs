// Put the demo on the explorer: a paid claim, an unpaid claim, and the refusals.
//
//   pnpm demo:contract        (after pnpm deploy:contract)
//
// The page the bounty is on is demo/page.html in this repository, read through
// raw.githubusercontent.com. It lists three links: one that answers 200, one
// that answers 404 because the file was never committed, and one the page does
// not list at all. The validators fetch the page and the link themselves.

import { readFileSync, writeFileSync } from "node:fs";
import { RECORD_FILE, accountFrom, balanceOf, clientFor, fundAccount, loadEnv, network, readRetry, recorder, sleep, sourceSha256 } from "./lib.mjs";

loadEnv();
const net = network();
const record = JSON.parse(readFileSync(RECORD_FILE, "utf8"));
if (record.source_sha256 !== sourceSha256()) throw new Error("linkcheck.py changed since it was deployed; run pnpm deploy:contract first");
const address = record.contract;
const GEN = 10n ** 18n;
const RAW = "https://raw.githubusercontent.com/Elinajafari/linkcheck/main/demo";
const PAGE = `${RAW}/page.html`;
const LIVE = `${RAW}/alive.txt`;
const DEAD = `${RAW}/gone.txt`;
const UNLISTED = `${RAW}/never-listed.txt`;
const REWARD = GEN / 100n; // 0.01 GEN

const ROLES = ["owner", "ann", "ben"];
const who = Object.fromEntries(ROLES.map((k) => [k, accountFrom(`${k.toUpperCase()}_PRIVATE_KEY`, net)]));
const as = Object.fromEntries(ROLES.map((k) => [k, clientFor(net, who[k])]));
const ro = clientFor(net, null);
const read = (fn, args = []) => readRetry(() => ro.readContract({ address, functionName: fn, args }));
const plain = (v) => JSON.parse(JSON.stringify(v, (_, x) => (typeof x === "bigint" ? Number(x) : x instanceof Map ? Object.fromEntries(x) : x)));
const rec = recorder();

console.log(`\ncontract ${address}`);
if (Number(await read("count")) !== 0) throw new Error("the demo expects a fresh contract");
if (net.name === "studionet") await fundAccount(net, ro, who.owner.address, GEN);

const annBefore = await balanceOf(ro, who.ann.address);
const w = (k, fn, args, opts) => rec.write(as[k], who[k], address, fn, args, opts);

await w("owner", "open", [PAGE, 3600n], { step: "owner pays the reward in for the demo page", value: REWARD });
await w("ben", "claim", [0n, LIVE], { step: "a link the page lists that answers 200: tried, not dead, not paid" });
await w("owner", "claim", [0n, DEAD], { step: "the owner claims on their own bounty: refused", expect: "refused" });
await w("ann", "claim", [0n, UNLISTED], { step: "a link the page never lists: tried, not paid" });
await w("ben", "claim", [0n, LIVE], { step: "the same link again: refused for free", expect: "refused" });
await w("ann", "claim", [0n, DEAD], { step: "a listed link that answers 404: paid at once" });
await w("ben", "claim", [0n, DEAD], { step: "the bounty is already paid: refused", expect: "refused" });
await w("ben", "settle", [0n], { step: "settle on a paid bounty: refused", expect: "refused" });

let annAfter = await balanceOf(ro, who.ann.address);
for (let i = 0; i < 20 && annAfter <= annBefore; i++) { await sleep(10_000); annAfter = await balanceOf(ro, who.ann.address); } // the payout lands after the receipt
record.steps.push(...rec.steps);
record.demo = {
  page: PAGE, listed_alive: LIVE, listed_dead: DEAD, never_listed: UNLISTED,
  reward_wei: String(REWARD),
  bounty: plain(await read("bounty", [0n])),
  claims: plain(await read("claims_of", [0n])),
  held_after: String(await read("held_total")),
  winner_balance_before: String(annBefore), winner_balance_after: String(annAfter),
  winner_gain: String(annAfter - annBefore),
  accounts: Object.fromEntries(ROLES.map((k) => [k, who[k].address])),
};
writeFileSync(RECORD_FILE, JSON.stringify(record, null, 2) + "\n");
console.log(`\nwinner gained ${annAfter - annBefore} wei; record written to ${RECORD_FILE}\n`);
