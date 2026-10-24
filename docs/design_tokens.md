# Master COM: design tokens

Source of truth for the design lab in the prototype artifact (version 9). All palettes pass WCAG AA: text 4.5:1, strong borders 3:1 (checked by script). The chosen variant becomes the Tailwind config of the real app.

**Current pick (R-014, tentative): variant 11, "Pick: 6 + fonts of 2".**

Shared: fonts load from Google Fonts with Tajik coverage verified (ғ ӣ қ ӯ ҳ ҷ); headings use the display font; buttons use the button radius; the portfolio grid uses the grid gap.

## 1. Quiet showcase (Тихая витрина) ★

- UI font: Geist; display font: Source Serif 4 (600); H1: `clamp(28px,4.6vw,40px)`, letter-spacing -0.01em
- Radii: button 999px, card 16px, input 12px, small 10px; portfolio grid gap 4px
- Default theme: system

| Token | Light | Dark |
|---|---|---|
| Background | `#F7F6F3` | `#141518` |
| Surface | `#FFFFFF` | `#1C1D21` |
| Surface 2 | `#EFEDE8` | `#25272C` |
| Text | `#16181D` | `#ECEDEF` |
| Muted text | `#5C6270` | `#A3A7B0` |
| Border | `#E4E1DA` | `#2E3036` |
| Strong border | `#8E8A82` | `#676A72` |
| Accent | `#1F3C8F` | `#7C95E6` |
| Accent hover | `#182F72` | `#98ACEE` |
| Text on accent | `#FFFFFF` | `#0E1430` |
| Accent tint | `#E6EBF7` | `#1E2747` |
| Text on tint | `#1F3C8F` | `#B7C5F3` |

## 2. Magazine (Журнал)

- UI font: Inter; display font: Cormorant Garamond (600); H1: `clamp(34px,6vw,56px)`, letter-spacing -0.01em
- Radii: button 6px, card 8px, input 6px, small 6px; portfolio grid gap 6px
- Default theme: system

| Token | Light | Dark |
|---|---|---|
| Background | `#FAF8F4` | `#1A1714` |
| Surface | `#FFFDF9` | `#221E1A` |
| Surface 2 | `#F1EDE5` | `#2C2722` |
| Text | `#1B1B1B` | `#EFE9DF` |
| Muted text | `#625C55` | `#ADA597` |
| Border | `#E6E0D6` | `#3A342D` |
| Strong border | `#978D80` | `#72695E` |
| Accent | `#1E4D3B` | `#7FB89C` |
| Accent hover | `#163A2D` | `#9CCBB3` |
| Text on accent | `#FFFFFF` | `#10221A` |
| Accent tint | `#E3EDE7` | `#21332A` |
| Text on tint | `#1E4D3B` | `#A9D3BE` |

## 3. Norm (Норм)

- UI font: IBM Plex Sans; display font: EB Garamond (500); H1: `clamp(28px,4.4vw,40px)`, letter-spacing 0
- Radii: button 999px, card 8px, input 8px, small 8px; portfolio grid gap 8px
- Default theme: system

| Token | Light | Dark |
|---|---|---|
| Background | `#ECEAE6` | `#1C1B19` |
| Surface | `#F6F4F0` | `#24231F` |
| Surface 2 | `#E2DFD9` | `#2E2C28` |
| Text | `#2B2A28` | `#E8E4DD` |
| Muted text | `#5F5A52` | `#A8A196` |
| Border | `#D8D3CB` | `#393631` |
| Strong border | `#8B8378` | `#76706A` |
| Accent | `#84573E` | `#C99779` |
| Accent hover | `#6E4732` | `#D8AE94` |
| Text on accent | `#FFFFFF` | `#23160F` |
| Accent tint | `#EADFD6` | `#3A2B21` |
| Text on tint | `#6E4632` | `#E3C3AE` |

## 4. Linear (Линейный) ★

- UI font: Geist; display font: Geist (600); H1: `clamp(28px,4.6vw,44px)`, letter-spacing -0.02em
- Radii: button 999px, card 12px, input 10px, small 8px; portfolio grid gap 2px
- Default theme: dark

| Token | Light | Dark |
|---|---|---|
| Background | `#FBFBFC` | `#0F1013` |
| Surface | `#FFFFFF` | `#17181C` |
| Surface 2 | `#F1F2F5` | `#1F2025` |
| Text | `#16171B` | `#E8E9EC` |
| Muted text | `#5E616B` | `#9A9CA5` |
| Border | `#E3E4E8` | `#26272D` |
| Strong border | `#8B8F99` | `#676A72` |
| Accent | `#4B57D6` | `#7E89FF` |
| Accent hover | `#3C47B8` | `#99A2FF` |
| Text on accent | `#FFFFFF` | `#0B0D24` |
| Accent tint | `#E8EAFC` | `#1E2142` |
| Text on tint | `#3A44B0` | `#B9BFFF` |

## 5. Friendly (Дружелюбный)

- UI font: Onest; display font: Onest (800); H1: `clamp(30px,5.4vw,48px)`, letter-spacing -0.01em
- Radii: button 18px, card 20px, input 16px, small 14px; portfolio grid gap 6px
- Default theme: system

| Token | Light | Dark |
|---|---|---|
| Background | `#FFFFFF` | `#121816` |
| Surface | `#FFFFFF` | `#1A2420` |
| Surface 2 | `#E7F5EF` | `#22302A` |
| Text | `#1A1F1C` | `#E6EFEA` |
| Muted text | `#56625C` | `#A0B0A8` |
| Border | `#DDE7E2` | `#2C3A34` |
| Strong border | `#85948D` | `#62746C` |
| Accent | `#0B6B4F` | `#4FC596` |
| Accent hover | `#08563F` | `#72D3AD` |
| Text on accent | `#FFFFFF` | `#06281C` |
| Accent tint | `#E7F5EF` | `#173A2D` |
| Text on tint | `#0B5A43` | `#9FE3C6` |

## 6. Typographic (Шрифтовой)

- UI font: Geologica; display font: Geologica (800); H1: `clamp(40px,8vw,84px)`, letter-spacing -0.03em
- Radii: button 12px, card 12px, input 12px, small 10px; portfolio grid gap 2px
- Default theme: system

| Token | Light | Dark |
|---|---|---|
| Background | `#FFFFFF` | `#0B0B0B` |
| Surface | `#FFFFFF` | `#151515` |
| Surface 2 | `#F2F2F2` | `#1F1F1F` |
| Text | `#111111` | `#F2F2F2` |
| Muted text | `#575757` | `#A3A3A3` |
| Border | `#E2E2E2` | `#2A2A2A` |
| Strong border | `#8A8A8A` | `#656565` |
| Accent | `#111111` | `#F5C400` |
| Accent hover | `#333333` | `#FFD633` |
| Text on accent | `#FFFFFF` | `#111111` |
| Accent tint | `#FFF1B8` | `#3A3000` |
| Text on tint | `#3D3200` | `#FFE066` |

## 7. Upbeat (Бодрый)

- UI font: Commissioner; display font: Commissioner (700); H1: `clamp(30px,5vw,46px)`, letter-spacing -0.01em
- Radii: button 999px, card 18px, input 14px, small 12px; portfolio grid gap 6px
- Default theme: system

| Token | Light | Dark |
|---|---|---|
| Background | `#FFFFFF` | `#151718` |
| Surface | `#FFFFFF` | `#1E2123` |
| Surface 2 | `#F2F4F5` | `#272B2E` |
| Text | `#141618` | `#ECEFF1` |
| Muted text | `#596066` | `#A2AAB0` |
| Border | `#E3E7EA` | `#30353A` |
| Strong border | `#899299` | `#676F74` |
| Accent | `#1C7F4A` | `#4CC386` |
| Accent hover | `#166A3D` | `#6FD19E` |
| Text on accent | `#FFFFFF` | `#06281A` |
| Accent tint | `#E3F4EA` | `#173826` |
| Text on tint | `#16653B` | `#9BE2BE` |

## 8. Workshop (Мастерская)

- UI font: PT Sans; display font: Alegreya (700); H1: `clamp(30px,5vw,46px)`, letter-spacing 0
- Radii: button 12px, card 12px, input 10px, small 10px; portfolio grid gap 6px
- Default theme: system

| Token | Light | Dark |
|---|---|---|
| Background | `#F4ECE0` | `#1D1712` |
| Surface | `#FBF7F1` | `#271F18` |
| Surface 2 | `#EDE2D2` | `#31281F` |
| Text | `#4A3324` | `#EFE6DA` |
| Muted text | `#6E5644` | `#B3A491` |
| Border | `#E3D6C3` | `#3E332A` |
| Strong border | `#988269` | `#7B6B5C` |
| Accent | `#0F6F72` | `#4FB5B7` |
| Accent hover | `#0D5F62` | `#72C6C8` |
| Text on accent | `#FFFFFF` | `#06292A` |
| Accent tint | `#DDEFEE` | `#173739` |
| Text on tint | `#0D5F62` | `#9ED9DA` |

## 9. Community (Сообщество)

- UI font: Inter; display font: Lora (600); H1: `clamp(28px,4.6vw,40px)`, letter-spacing 0
- Radii: button 10px, card 12px, input 10px, small 8px; portfolio grid gap 4px
- Default theme: system

| Token | Light | Dark |
|---|---|---|
| Background | `#F3F4F6` | `#141518` |
| Surface | `#FFFFFF` | `#1C1D21` |
| Surface 2 | `#ECEEF1` | `#25272C` |
| Text | `#15171A` | `#ECEDEF` |
| Muted text | `#5A5F68` | `#A3A7B0` |
| Border | `#E1E3E7` | `#2E3036` |
| Strong border | `#878C95` | `#676A72` |
| Accent | `#1F3C8F` | `#7C95E6` |
| Accent hover | `#182F72` | `#98ACEE` |
| Text on accent | `#FFFFFF` | `#0E1430` |
| Accent tint | `#E6EBF7` | `#1E2747` |
| Text on tint | `#1F3C8F` | `#B7C5F3` |

## 10. Blueprint (Чертёж) ★

- UI font: IBM Plex Sans; display font: IBM Plex Sans (600); H1: `clamp(28px,4.6vw,42px)`, letter-spacing -0.01em
- Radii: button 999px, card 8px, input 8px, small 6px; portfolio grid gap 4px
- Default theme: system

| Token | Light | Dark |
|---|---|---|
| Background | `#FFFFFF` | `#0E1A2E` |
| Surface | `#FFFFFF` | `#13223A` |
| Surface 2 | `#F2F5F9` | `#1A2C48` |
| Text | `#23262B` | `#E3ECF8` |
| Muted text | `#59606B` | `#9DB0CB` |
| Border | `#DCE3EC` | `#243A5C` |
| Strong border | `#8794A5` | `#4F6E99` |
| Accent | `#1F4FA3` | `#7FB0FF` |
| Accent hover | `#183F84` | `#9FC4FF` |
| Text on accent | `#FFFFFF` | `#08162C` |
| Accent tint | `#E5EDF8` | `#1A3358` |
| Text on tint | `#1A4590` | `#B5D1FF` |
| Drawing grid | `#E9EEF5` | `#172943` |

## 11. Pick: 6 + fonts of 2 (Выбор: 6 + шрифты 2) ★

- UI font: Inter; display font: Cormorant Garamond (700); H1: `clamp(44px,8.6vw,92px)`, letter-spacing -0.01em
- Radii: button 12px, card 12px, input 12px, small 10px; portfolio grid gap 2px
- Default theme: system
- Signature details: same as typographic

| Token | Light | Dark |
|---|---|---|
| Background | `#FFFFFF` | `#0B0B0B` |
| Surface | `#FFFFFF` | `#151515` |
| Surface 2 | `#F2F2F2` | `#1F1F1F` |
| Text | `#111111` | `#F2F2F2` |
| Muted text | `#575757` | `#A3A3A3` |
| Border | `#E2E2E2` | `#2A2A2A` |
| Strong border | `#8A8A8A` | `#656565` |
| Accent | `#111111` | `#F5C400` |
| Accent hover | `#333333` | `#FFD633` |
| Text on accent | `#FFFFFF` | `#111111` |
| Accent tint | `#FFF1B8` | `#3A3000` |
| Text on tint | `#3D3200` | `#FFE066` |
