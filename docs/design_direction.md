# Design direction v1 (2026-10-23)

Based on the owner's references. Status: proposal, awaiting confirmation.

## Direction: "Editorial showcase"
The calm, photo-first interface of Savee and Houzz, plus the large editorial photo galleries of The Modern House, Inigo and Norm Architects. The UI stays quiet so the masters' work photos lead. Motion is minimal: only short transitions and native swipe, no decorative animation.

## Screen by screen

| Screen | Reference | What we take | What we leave |
|---|---|---|---|
| Home | TaskRabbit; Houzz category tiles (owner's screenshot) | One large search (specialty + district); category tiles: photo with a white pill label, rounded corners; "How it works" in 3 steps; "Post a request" CTA | US-style stock portraits: we use photos of real local masters or their work |
| Search results | Houzz search results | Master cards with a strip of work photos, rating, district, experience, "Contact"; filters as pill chips on top, full filters in a side panel (desktop) or bottom sheet (phone) | Ads and sponsored slots |
| Feed | Contra "For you" | Center feed of projects and requests, left navigation, right panel with filters and suggestions on desktop; bottom tab bar on phone | Social metrics clutter |
| Project (one portfolio collection) | The Modern House, Inigo, Norm Architects | Full-width photos, horizontal swipe with snap, counter "3 / 12", thumbnails below, short description and the master's card | Heavy scroll effects |
| Project list with descriptions | inspire.savee.com | Grid of projects, each with a cover, title and one line of description | — |
| Sign-in / registration | Savee | Split screen on desktop: a work photo on one side, a minimal form on the other; large Google and Telegram buttons first, email below | — |
| Company pages | Norm Architects, Bolt, Pangram Pangram, Linear | Big confident typography, team, projects, services (v2) | — |
| Products / materials | Poltrona Frau | Noted for a possible future catalog; not in v1 or v2 | — |

## Visual system

- **Canvas:** warm off-white, like the Houzz screenshot background; white cards; near-black ink.
- **Accent:** one deep lapis blue, used sparingly: primary button, links, active filter.
- **Semantic colors:** separate from the accent; success, warning and error only.
- **Dark theme (v1):** Linear-like: deep graphite, not pure black; photos keep their colors; the accent is lightened for contrast.
- **Shapes:**
  - buttons, search field and filter chips are pills (Savee, TaskRabbit);
  - cards and photo tiles use 12–16 px radius.
- **Photos:**
  - square 1:1 grid in profiles;
  - full uncropped photo in the project gallery.
- **Typefaces** (all checked against the full Tajik alphabet ғ ӣ қ ӯ ҳ ҷ, see below):
  - **UI and body: Geist.** Crisp and neutral, in the spirit of Savee and Linear.
  - **Editorial headings** (project pages, SEO pages, home hero): **Source Serif 4.** Gives the "magazine" feel of The Modern House.

### Font check: Tajik letters (Google Fonts files from github.com/google/fonts)
- **Full support:**
  - sans: Geist, Inter, Onest, Golos Text, Geologica, Commissioner, IBM Plex Sans, Montserrat, Noto Sans, PT Sans;
  - serif: Source Serif 4, Lora, PT Serif, EB Garamond, Cormorant Garamond, Alegreya.
- **Missing all Tajik letters:** Manrope, DM Sans, Plus Jakarta Sans, Instrument Sans, Jost, Unbounded, Fraunces, Tenor Sans.
- **Missing some Tajik letters:** Playfair Display and Literata (no ӣ ӯ ҳ ҷ); Rubik and Prata (no ҳ).

## Logo direction (for the designer brief)
- Wordmark in Latin plus a simple symbol.
- The symbol can also live large in the background with the name in front, as on posters, the app splash screen and social covers.
- **Symbol idea:** built on a square grid from Central Asian geometric ornament (girih, tile patterns), reduced to a few strokes so it reads at 16 px.
- **References:**
  - Renault by Landor: a flat geometric element that works as an outline;
  - Tempo by Studio Dumbar: name plus element as one system;
  - Hiut Denim by Pentagram: a warm, crafted wordmark;
  - 520 Fifth Avenue by Pentagram: architectural precision.

## Next
Three key screens (home, search results, project gallery) in light and dark, for review; then the logo brief.
