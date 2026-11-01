# firstclub-mcp

An MCP server for [FirstClub](https://firstclub.co.in), the grocery app in Bengaluru. I use it with Claude to search what's in stock near me, check prices and lab reports, look back at what I've ordered, and put together shopping lists for meal prep.

> This is not an official FirstClub thing. It talks to the same API the Android app uses (worked out from app version 2.1.57). It can stop working whenever they change something on their side. Use it for your own account, at a normal pace.

## What it can do

- log in with your phone number and the SMS OTP
- pick one of your saved delivery addresses, or add a new one
- search products, browse categories, get product details and other pack sizes
- get the lab report PDFs FirstClub publishes for a lot of products (protein, fat, contaminants etc.)
- read your FirstClub food profile (veg / high protein / supplements and so on)
- order history, a spend summary, "buy again", live tracking for an order
- wallet balance and cashback
- save products into a wishlist collection that shows up in your app
- its own cart, with coupons and the free gift / special price deals that unlock as the cart grows
- pay for that cart from the FirstClub wallet (wallet only, never card or UPI)

> The cart in this MCP is **not** the cart in your phone app. FirstClub keeps one cart per device, and the MCP counts as its own device. If you want items to end up in your app, use a wishlist collection (Profile → Wishlist in the app) and add them to your cart from there.

> Wallet checkout has not been tested against a real order yet. It only goes ahead when your wallet's spendable balance covers the whole bill. Before paying it shows you the items, the total and the delivery address and waits for a yes. If your client can't show that prompt, it only orders when you've told Claude in so many words to place the order.

## Before you start

You need [uv](https://docs.astral.sh/uv/). It picks up Python 3.12 on its own.

```sh
brew install uv
```

Your login is saved in `~/.secrets/firstclub/session.json` (folder 700, file 600). To keep it somewhere else, set `FIRSTCLUB_SECRETS_DIR`.

## Install

It runs over stdio only, on your own machine. There is no hosted or remote version, and there won't be one.

### Claude Code

From a clone of this repo:

```sh
git clone https://github.com/frunkad/firstclub-mcp ~/Projects/firstclub-mcp
claude mcp add --scope user firstclub -- uv --directory ~/Projects/firstclub-mcp run firstclub-mcp
```

Or without keeping a clone around (uv fetches and caches it):

```sh
claude mcp add --scope user firstclub -- uvx --from git+https://github.com/frunkad/firstclub-mcp firstclub-mcp
```

Drop `--scope user` if you only want it in the current project. `claude mcp list` should show it as connected.

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (on Windows it's `%APPDATA%\Claude\claude_desktop_config.json`) and add:

```json
{
  "mcpServers": {
    "firstclub": {
      "command": "/opt/homebrew/bin/uv",
      "args": ["--directory", "/Users/you/Projects/firstclub-mcp", "run", "firstclub-mcp"]
    }
  }
}
```

Restart Claude Desktop after saving.

> Use the full path to `uv` (`which uv` tells you). Desktop apps don't get your shell's PATH, so a bare `uv` usually fails.

### Cursor, Windsurf, Zed, and other MCP clients

Any client that can launch a stdio server works. The command is the same everywhere:

```
command: uv
args:    --directory /path/to/firstclub-mcp run firstclub-mcp
```

or, without a clone:

```
command: uvx
args:    --from git+https://github.com/frunkad/firstclub-mcp firstclub-mcp
```

Cursor reads it from `~/.cursor/mcp.json` in the same `mcpServers` format as Claude Desktop above.

### Just running it

```sh
cd firstclub-mcp
uv run firstclub-mcp
```

It sits there waiting for an MCP client on stdin/stdout. Mostly useful to check that it starts.

> claude.ai on the web and the Claude mobile app can only use remote MCP servers, so this one won't show up there.

## First run

Ask Claude to log in to FirstClub. It will:

1. ask for your mobile number (Indian numbers only, same as the app) and then the OTP from the SMS
2. show your saved addresses to pick from, with an option to add a new one
3. pull your FirstClub food profile and tell you what it found, so you can say if something changed

If your client can't show those pickers, Claude just asks you in the chat instead.

> Logging in here can log you out of the app on your phone. Log back in there if that happens.

## Optional settings

| Variable | What it does |
|---|---|
| `FIRSTCLUB_SECRETS_DIR` | where the session file lives (default `~/.secrets/firstclub`) |
| `FIRSTCLUB_GUEST_ID` | the app's shared guest id. Only needed for browsing categories while logged out. In a clone it can also go in a `.guest_id` file at the repo root (gitignored). |

## Tools

| Area | Tools |
|---|---|
| login | `login`, `login_send_otp`, `login_verify_otp`, `logout`, `status` |
| address | `list_addresses`, `set_location`, `find_place`, `add_address`, `check_serviceability` |
| catalog | `search_products`, `browse_category`, `list_categories`, `get_products`, `get_variants`, `get_lab_reports` |
| you | `dietary_profile`, `list_orders`, `get_order`, `order_history_summary`, `buy_again`, `track_order`, `wallet_balance` |
| wishlist | `list_wishlist`, `save_to_collection`, `remove_from_wishlist` |
| cart | `cart_view`, `cart_set`, `cart_offers`, `claim_offer`, `unclaim_offer`, `list_coupons`, `apply_coupon`, `remove_coupon` |
| checkout | `checkout_preview`, `checkout_with_wallet` |

Every cart and wishlist tool says where things went (the MCP's cart, or a wishlist collection you can see in the app), and Claude is told to repeat that to you.

## Things that will trip you up

> Cafe items (the FirstClub Cafe stuff) only show up when the Cafe is serving your address at that time of day. Search with `vertical=CAFE`.

> Lab report links expire about 10 minutes after you ask for them. Ask again for a fresh one.

> FirstClub has no nutrition field on products. The lab reports and the pack photos are what there is.

> Coupons and deals only apply to the MCP's own cart, not the one in your app.

## Testing a tool by hand

```sh
uv run scripts/smoke.py                                   # list tools
uv run scripts/smoke.py search_products '{"query":"oats"}'
```
