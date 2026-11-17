# Linkcheck

A bounty paid to the first person who proves a link on a page is dead.

A site owner pays a reward in for one public page. Anyone who finds a broken link on it names that link. The contract does not take their word and does not ask a model. In the same transaction, every validator **fetches the page** and checks the link is really written in it, then **fetches the link** and reads the HTTP status the server answers with. A link the page lists whose server answers `404` or `410` is dead, and the reward is paid to whoever named it, at once. If nobody does within the window, the reward goes back to the owner.

## Why it is different

* **No prompt, nothing to inject.** The decision is a status code, not a sentence. Nothing a page says can change what it is worth.
* **Two fetches, one exact answer.** Each validator repeats both fetches and must agree on two facts: *is the link listed* and *is the status dead*.
* **Only a clear 404 or 410 pays.** A timeout, a 403 for robots, a 5xx or a redirect is "not proven dead", so an unreliable server can never cost the owner a reward.
* **Free refusals first.** Past the deadline, a settled bounty, the owner's own claim, a link already tried, the page itself, a non-http(s) address: all refused before any fetch.

```
open(page, window)  payable   owner pays the reward in
claim(id, link)               anyone but the owner, once per link
settle(id)                    after the deadline, returns the reward if nobody found a dead link
```

## Run it

```bash
pnpm install
pnpm test               # 49 tests against a GenVM simulator
pnpm lint:genvm         # genvm-lint on the contract
pnpm deploy:contract    # deploy to StudioNet from a throwaway wallet
pnpm demo:contract      # a paid claim, an unpaid claim and the refusals, on the explorer
pnpm verify:contract    # the source on chain is the file in this repository
```

The demo page is [`demo/page.html`](demo/page.html), read through `raw.githubusercontent.com`. It lists a link that answers 200 and one that answers 404.

## On chain

See [`packages/contracts/deployments/studionet.json`](packages/contracts/deployments/studionet.json) for the address, every transaction hash, each refusal sentence and the winner's balance before and after.

MIT.
