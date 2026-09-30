# WhatsApp setup, step by step

**Start with Telegram if you can.** It needs one token and nothing else, works on a laptop
behind any router, and nothing in it expires. WhatsApp is worth the extra work when the
people you serve are on WhatsApp, and it runs best on a server that stays on.

This guide follows Meta's developer dashboard as it looked on **2026-09-30**. Meta moves
these screens around; if a name here no longer matches, the values you need are the same.

## What you will collect

Two different things are called "token". Keep them apart; mixing them up is the most common
reason setup fails.

| Value | Looks like | Where it comes from | Where it goes |
|---|---|---|---|
| **Access token** | very long, starts `EAA…` | Meta, Step 1 > Generate token | your `.env` (`WHATSAPP_TOKEN`) |
| **Verify token** | short, 22 characters | `agronaut setup` makes it for you | Meta's webhook form, Step 2 |
| Phone Number ID | long number (not a phone number) | Meta, Step 1 | your `.env` |
| WhatsApp Business account ID | long number | Meta, Step 1 | your `.env` |
| App secret | 32 characters | Meta, App settings > Basic | your `.env` |
| Your own WhatsApp number | e.g. `886912345678` | your phone | your `.env` and Meta's recipient list |

`agronaut setup` asks for each of these in this order and writes the `.env` for you.

## 1. Create the app (once)

1. Go to **developers.facebook.com** > **My Apps** > **Create app**.
2. Choose the use case **Connect on WhatsApp**, and link or create a business portfolio
   when asked.
3. You land on **Use cases > Connect on WhatsApp > Customize**. The left panel shows
   **Basic setup: Step 1. Try it out, Step 2. Production setup, Step 3. Business
   verification**. You only need Steps 1 and 2 to talk to the bot yourself.

## 2. Step 1. Try it out: the test number and its values

On **Step 1. Try it out**, under **Claim a WhatsApp test number**:

- **Test number**: this is the bot's number, something like `+1 (555) 198-5979`. You will
  message it from your own phone.
- **Phone Number ID** and **WhatsApp Business account ID**: copy both.
- **Access token > Generate token**: copy the long `EAA…` token.

Under **Send a message from your test number**, open the **To** (recipient) list and add
**your own WhatsApp number**, then confirm it with the code Meta sends you. Meta's test
number only talks to numbers on this list.

> The **Send message** button on this screen sends a template *from* the test number *to*
> you. It does not test the bot. To test the bot, type a message in the WhatsApp app on your
> phone.

## 3. App secret

**App settings > Basic > App secret**, click **Show**, copy it. With it, the bot checks
that every message really came from Meta.

## 4. Run setup on your computer

```bash
agronaut setup
```

Choose your model, then **WhatsApp** as the channel, and paste the values from steps 2 and
3 when asked. Your own number goes in **country code first, digits only, no leading 0**:
Taiwan `0912 345 678` is `886912345678`, Burkina Faso `70 12 34 56` is `22670123456`.

Setup checks the values with Meta straight away and says what is wrong if anything is.
It keeps your verify token if you run it again, so Meta's saved copy stays valid.

## 5. Start the bot with a public address

Meta delivers messages by calling your computer over HTTPS. On a laptop, one command starts
the bot and a tunnel together (it needs `cloudflared`: `brew install cloudflared` on a Mac):

```bash
agronaut whatsapp --tunnel
```

It prints a box like this:

```
  Paste these into Meta, then click 'Verify and save':
    ... > Step 2. Production setup > Configure Webhooks

    Callback URL   https://some-random-words.trycloudflare.com/webhook
    Verify token   Xy7kQ2mZpL9vRt3wNa8bCd
```

**Keep this window open.** Closing it takes the address down.

## 6. Step 2. Production setup > Configure Webhooks

1. Open **Step 2. Production setup** and expand **Configure Webhooks**.
2. **Callback URL**: select everything in the box (Cmd+A / Ctrl+A) and paste the Callback
   URL from the box above.
3. **Verify token**: select everything in the box and paste the verify token from the box
   above. Not the access token.
4. Click **Verify and save**. The bot window shows `webhook verification OK`.
5. Under **Webhook fields**, make sure **messages** shows **Subscribed**.

A yellow note on this screen says unpublished apps only receive test webhooks. On
2026-09-30 messages from a number on the Step 1 recipient list were delivered to an
unpublished app all the same; publishing is only needed for other people.

## 7. Message the bot

In the WhatsApp app on your phone, open a chat with the test number and send **hi**.
The bot window logs the message and the bot replies.

## When it does not answer

Run the check. It tests the token, the account, the webhook and your number, and says
which one is wrong:

```bash
agronaut whatsapp --check --url https://some-random-words.trycloudflare.com/webhook
```

The bot window also says why it ignored something:

| The bot window says | What it means | Fix |
|---|---|---|
| `verification REFUSED … Meta sent N characters, .env has 22` | the token in Meta's form is an old one | paste the verify token again (step 6) |
| `… looks like the WhatsApp ACCESS token` | the access token went in the verify box | paste the short verify token instead |
| `… Meta sent an empty verify token` | a browser opened the address, or the box was empty | nothing, or fill the box (step 6) |
| `message from a number ending NN DROPPED` | your number is not in `AGRONAUT_ALLOWED_IDS` | fix it in `.env` (digits only, no leading 0) and restart |
| `delivery status update(s) … failed (error …)` | a reply could not be delivered | the error code says why; often the recipient list |
| nothing at all | Meta is calling an old address | paste the current Callback URL again (step 6) |
| `token rejected by the Graph API` in `--check` | the access token expired (daily) | see below |

## Every day: the access token expires

Meta's test access token lasts about a day. Generate a new one (Step 1 > Access token >
Generate token) and run:

```bash
agronaut whatsapp --token
```

Paste it (it stays hidden), and it is checked with Meta before it replaces the old one.
Restart the bot afterwards.

## For a bot that stays up

Two things make the laptop setup temporary: the tunnel address changes every time it
starts, and the test token expires daily.

- **A permanent token.** Meta's steps (from its
  [Get Started guide](https://developers.facebook.com/documentation/business-messaging/whatsapp/get-started),
  Step 5): in **Business Settings > System users**, add a system user, **Assign assets**
  (your app with *Manage app*, your WhatsApp account with *Manage WhatsApp Business
  accounts*), then **Generate token** with the permissions `business_management`,
  `whatsapp_business_messaging` and `whatsapp_business_management`. Save it with
  `agronaut whatsapp --token`.
- **A fixed address.** Run Agronaut on a server with a domain and a certificate, or use a
  named Cloudflare tunnel, and register that address once.
