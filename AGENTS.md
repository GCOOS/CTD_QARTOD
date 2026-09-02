8 AI Coding Agent Rules

No backward compatibility Delete deprecated code. Don’t add compatibility layers, don’t write migrations, don’t leave fallbacks. The codebase moves forward.

Simplest implementation that works Pick the minimal solution that satisfies current requirements. No premature abstractions. No unnecessary config layers. No “just in case” complexity.

Ship end-to-end first, then layer up Get a minimal end-to-end flow working before adding features. Never tear down something that already works for the sake of unfinished complexity.

Keep components modular with clear separation of concerns One thing, one place, one responsibility. Don’t let concerns bleed across modules.

Prefer mature, well-maintained libraries Use battle-tested dependencies. Don’t reinvent the wheel without a clear, documented reason.

Exhaust existing dependencies before adding new ones Check what’s already in the project before reaching for a new package or writing it yourself. Don’t assume the library can’t do it — verify first.

Make long-term architectural decisions No “we’ll fix it later” temporary solutions. Every architectural choice should be sustainable. If you wouldn’t ship it to production, don’t ship it to main.

Start from proven patterns, not from scratch Look at how mature products solve the same problem. Use validated patterns. Don’t invent from zero.
