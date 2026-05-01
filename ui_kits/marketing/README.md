# AutoTreeGen — Marketing UI kit

Recreation of the marketing surface (`apps/landing` in TreeGen). Coming-soon landing → waitlist → privacy. The hero is anchored by the haplogroup-ribbon tree.

- `index.html` — assembled landing (hero, value props, signature visual, pricing teaser, waitlist).
- Components are kept inline in the index — small, well-factored sections rather than a Storybook split (the real apps/landing also keeps each section in a single file).

Stack mirror: Next.js 15 + Tailwind 4 in production. Here we use the design system's `colors_and_type.css` directly.
