# Figma Make prompt: Papyrus Goals

Design a new Goals experience for **Papyrus**, a cross-platform book library and reader for physical and digital books. The purpose is to help people understand their reading progress and build a reading habit. Make it feel like a thoughtful part of a reading app: clear, calm, approachable, and easy to use.

Focus on the visual design and overall experience. You have freedom to rethink the composition, hierarchy, and interactions. Do not turn this into a dense dashboard or an exhaustive feature specification.

## Design style

Use **modern, minimal product design grounded in Material Design 3**, with the warmth and restraint of a well-designed reading app. Favor clean sans-serif typography, clear hierarchy, generous but deliberate spacing, subtle surface differences, and consistent rounded controls. Use simple outline icons from one coherent icon family.

The result should feel polished and practical. Avoid decorative gradients, glass effects, oversized statistics, excessive shadows, motivational slogans, and unnecessary visual clutter. Let typography, alignment, and spacing do most of the work.

Choose a cohesive layout rather than forcing every piece of content into a card. Where you use repeated cards or controls, keep their proportions and alignment consistent. Buttons should fit their purpose and labels; short inputs should not stretch across a large desktop panel. Keep activity information easy to scan without repeating long book titles unnecessarily.

## Colors and themes

Provide **Light and Dark** versions using the same visual language and layout. Use Papyrus’s existing palette:

| Role | Light | Dark |
| --- | --- | --- |
| Primary accent | `#5654A8` | `#C3C0FF` |
| Text on primary | `#FFFFFF` | `#272377` |
| Background | `#FFFBFF` | `#1C1B1F` |
| Main text | `#1C1B1F` | `#E5E1E6` |
| Secondary text | `#47464F` | `#C8C5D0` |
| Subtle borders | `#C8C5D0` | `#47464F` |
| Soft accent surface | `#E2DFFF` | `#3E3C8F` |

Use purple sparingly to guide attention and indicate selection. Keep most surfaces neutral and maintain readable contrast. Also show how the design adapts to a high-contrast, grayscale e-ink display with solid surfaces and minimal motion.

## Cross-platform design

This is a **Flutter application for Android, iOS, web, Windows, macOS, and Linux**. Design it as one coherent product that adapts naturally to phones, tablets, and desktops, rather than a desktop dashboard squeezed onto a phone.

Show representative phone, tablet, and desktop layouts. Account for touch, mouse, keyboard, visible focus, larger text, and system safe areas. Use bottom navigation on phones and sidebar navigation on wide screens as surrounding app context. Adapt secondary interactions to suitable mobile sheets and desktop dialogs or panels. Keep essential actions easy to reach without consuming excessive space.

## Deliverable

Create a visually convincing, interactive prototype of the Goals experience, including a representative overview, an activity/history view, and a secondary interaction that demonstrates the shared component style. Use concise, natural copy and realistic sample content, including a long book title, to demonstrate a balanced layout.

Prioritize visual coherence, readability, and a strong responsive composition over the number of features shown. Establish a reusable typography, spacing, surface, and control system that could also guide the rest of Papyrus.
