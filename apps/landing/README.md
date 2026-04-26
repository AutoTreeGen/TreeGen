# @autotreegen/landing

Coming-soon landing page for **autotreegen.com**. Tagline: *From DNA to truth.*

Static Next.js 15 site (App Router) deployed to Cloudflare Pages.
Waitlist endpoint runs as a Cloudflare Pages Function with KV storage.

## Tech stack

- **Framework:** Next.js 15 (App Router, static export)
- **Styling:** Tailwind CSS 4 (CSS-first `@theme` tokens)
- **Components:** Custom shadcn-style primitives + Radix UI for a11y
- **Animations:** [motion](https://motion.dev) (Framer Motion successor)
- **Icons:** [lucide-react](https://lucide.dev)
- **Hosting:** Cloudflare Pages (static) + Pages Functions (waitlist API)
- **Storage:** Cloudflare Workers KV (`WAITLIST` namespace)

## Local development

```bash
# from monorepo root
pnpm install                       # installs all workspace deps
pnpm -F @autotreegen/landing dev   # http://localhost:3001
pnpm -F @autotreegen/landing build # static export → apps/landing/out/
```

## Project structure

```
apps/landing/
├── src/
│   ├── app/
│   │   ├── layout.tsx           # root layout, fonts, SEO
│   │   ├── page.tsx             # landing page (composition)
│   │   ├── privacy/page.tsx     # privacy notice
│   │   └── globals.css          # Tailwind 4 + design tokens
│   ├── components/
│   │   ├── hero.tsx             # animated hero
│   │   ├── gradient-orb.tsx     # decorative purple orbs
│   │   ├── section-shell.tsx    # reveal-on-scroll wrapper
│   │   ├── feature-card.tsx     # 3-up cards with stagger
│   │   ├── problem-section.tsx
│   │   ├── solution-section.tsx
│   │   ├── how-it-works.tsx
│   │   ├── waitlist-section.tsx
│   │   ├── waitlist-form.tsx    # form + state machine
│   │   ├── footer.tsx
│   │   └── ui/                  # Button, Input, Checkbox primitives
│   └── lib/utils.ts             # cn() helper
├── public/favicon.svg
├── next.config.ts               # output: 'export'
├── postcss.config.mjs           # tailwind 4 postcss plugin
└── package.json
```

The Pages Function lives at the **monorepo root** (Cloudflare convention):

```
functions/
└── api/
    └── waitlist.ts   # POST /api/waitlist → KV + optional Resend notify
```

## Deploying to Cloudflare Pages

### One-time setup

1. **Create the Pages project**
   - Cloudflare dashboard → Workers & Pages → Create → Pages → Connect to Git
   - Select your `TreeGen` repo
   - **Production branch:** `main`
   - **Build command:** `pnpm install --frozen-lockfile && pnpm -F @autotreegen/landing build`
   - **Build output directory:** `apps/landing/out`
   - **Root directory:** *(leave blank — repo root)*
   - **Environment variables:**
     - `NODE_VERSION=22`
     - `PNPM_VERSION=9.12.0`

2. **Create KV namespace**
   - Workers & Pages → KV → Create namespace → name: `autotreegen-waitlist`
   - Copy the namespace ID

3. **Bind KV to the Pages project**
   - Pages project → Settings → Functions → KV namespace bindings
   - **Variable name:** `WAITLIST`
   - **Namespace:** select `autotreegen-waitlist`
   - Bind to both **Production** and **Preview**

4. **(Optional) Wire up email notifications via Resend**
   - Sign up at [resend.com](https://resend.com) (free tier: 3k emails/month)
   - Add and verify your domain (`autotreegen.com`) — Resend gives you DNS records to add (SPF/DKIM). These coexist with Cloudflare Email Routing.
   - Create an API key
   - In Pages project → Settings → Environment variables, add:
     - `RESEND_API_KEY` = your key (mark as **Encrypted**)
     - `NOTIFICATION_TO` = `autotreegen@gmail.com`

5. **Attach custom domain**
   - Pages project → Custom domains → Set up custom domain
   - Enter `autotreegen.com`
   - Cloudflare auto-creates the CNAME — confirm
   - Add `www.autotreegen.com` too if desired (optional redirect)

### Deploy

After committing to `main`, Cloudflare auto-builds and deploys. Preview deploys
fire on every PR.

To test waitlist locally:

```bash
# install wrangler (one time)
pnpm dlx wrangler pages dev apps/landing/out --kv WAITLIST
```

## Customising

| What | Where |
|---|---|
| Brand colours | `src/app/globals.css` — `@theme` block |
| Headline / copy | `src/components/hero.tsx`, `*-section.tsx` |
| Privacy policy | `src/app/privacy/page.tsx` |
| Contact email | `src/components/footer.tsx`, `privacy/page.tsx` |
| Email validation rules / rate limit | `functions/api/waitlist.ts` |

## Roadmap

- [ ] Phase A — *current* — coming-soon landing + email waitlist
- [ ] Phase B — drag-drop GEDCOM upload (R2 + parser-service)
- [ ] Phase C — full evidence-graded analysis (Phase 2-3 of project ROADMAP)

See repo-level `ROADMAP.md` for the complete platform plan.
