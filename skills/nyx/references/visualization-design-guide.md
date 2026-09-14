# Nyx Visualization Design Guide

## User Design Preferences (CRITICAL)

The user has VERY specific aesthetic requirements for Nyx visualizations. Deviating from these produces immediate rejection.

### Core Requirements

1. **Dot-matrix wireframe construction**: The sandglass must be built from a grid of dots (point cloud) on the surface, with nearby dots connected by lines. NOT SVG paths, NOT geometric triangles. Think Tron-style wireframe.

2. **Complete sandglass shape**: Must be recognizable as a real sandglass - top bulb, narrow neck, bottom bulb. Not abstract or stylized.

3. **Physics-based particle movement**:
   - Gravity pulls particles down
   - Air resistance / damping
   - Turbulence (sin/cos-based random perturbations)
   - Particles bounce off interior walls
   - Neck accelerates particles
   - Top chamber: slow, floaty
   - Bottom chamber: accumulate with reduced velocity

4. **Data labels flow with particles**: Real data values (memory count, sessions, etc.) float alongside the particle stream, especially near the neck.

5. **Single centered element**: The screen contains ONLY the sandglass. No side panels, no charts, no metric cards cluttering the view.

6. **Minimal corner info**: Small text in corners showing aggregate stats (entries, sessions, days, dreams).

7. **Visual style**:
   - Background: very dark (#04040a)
   - Particles: blue-purple spectrum, glow halos
   - 3D rotation animation
   - Slight trail effect on canvas

### Anti-Patterns (DO NOT DO)

- Conventional dashboards with Chart.js / metric cards
- Static SVG shapes without dot-matrix construction
- Particles moving in straight lines without physics
- Multiple UI elements competing for attention
- Bright colors, light backgrounds
- "AI-generated" looking generic designs

### Technical Implementation

- Pure Canvas 2D rendering (no external libs)
- requestAnimationFrame loop
- 3D projection: rotate points around Y and X axes, perspective project
- Particle system: 200-300 particles
- Data labels: 6-8 floating text elements that cycle through real data
- Trail effect: semi-transparent fill instead of full clear each frame

### Example Data Points to Display

```
36,318  memories
1,504   sessions  
44,108  messages
1,804   days
40      dreams
peak    2026-06-18 (289)
uptime  4y 11m
```
