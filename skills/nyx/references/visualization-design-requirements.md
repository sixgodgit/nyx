# Nyx Sandglass Visualization Design Requirements

Source: Session feedback from user (2026-07-27/28). User rejected multiple attempts for being "太抽象" (too abstract), "一点都不好看" (not good looking at all), "常规太AI" (too conventional/AI-looking).

## User Aesthetic Preferences

- **Dark cyberpunk style**: background `#04040a` (near-black with blue tint)
- **Neon blue-purple palette**: primary colors `hsl(220-260, 80%, 60-80%)`
- **Minimal single-sandglass layout**: ONE sandglass centered on screen, nothing else in the main view
- **Four-corner info text**: minimal DOM text at corners (stats, clock)
- **No template-looking designs**: user explicitly rejects Chart.js-style dashboards

## Sandglass Construction (CRITICAL)

The sandglass MUST be constructed from a **dot-matrix wireframe** — points on the surface connected by lines. NOT abstract triangles or simple shapes.

- **300-400 vertices** on the surface
- **Two symmetric bulbs + narrow neck**: recognizable hourglass shape
- **3D rotation**: slow auto-rotation (~0.003 rad/frame) with perspective projection
- **Size**: viewport short-edge × 0.38 as base scale
- **Connection logic**: connect nearby dots (within threshold distance) with lines of varying opacity based on Z-depth

### Surface Point Generation
```
for theta in 0..2PI (20 steps)
  for phi in 0..PI (14 steps)
    // Top bulb
    r = bulbR * sin(phi) * (1 - (phi/PI) * 0.7)
    y = -(neckH + bulbH * cos(phi) + (1-cos(phi)) * neckH * 0.5)
    x = r * cos(theta), z = r * sin(theta)
    // Bottom bulb (mirror)
```

## Particles (5000)

- **5000 particles** filling the interior volume
- **Physics-based movement**: gravity, air resistance (damping ~0.992), turbulence (sine-wave noise)
- **Neck acceleration**: particles accelerate through the neck, slow in bottom bulb
- **Bottom-to-top recycling**: particles loop back to top after reaching bottom
- **Interior collision detection**: particles clamped to sandglass interior shape
- **Glow effects**: radial gradient glow + bright core per particle
- **Blue-purple spectrum**: hue 200-260

### Interior Collision Math
```
bulbR = S * 0.43, neckR = S * 0.10, neckH = S * 0.12, bulbH = S * 0.55
r = sqrt(x² + z²), absY = |y|
if absY < neckH: maxR = neckR * (1 - (absY/neckH)*0.3)
else: normalizedY = (absY - neckH)/bulbH; maxR = bulbR*sin(normalizedY*PI)*(1-normalizedY*0.7)
if r > maxR: scale x,z back to maxR-0.5
```

## Data Labels

- Float alongside the sand stream near the neck
- Show real memory data: memories count, sessions, messages, days, dreams, peak day
- Sub-labels below each value (e.g., "memories", "sessions")
- Glow effect (shadowBlur)
- Cycle through different data points as they loop

## Delegation Pattern

For complex visualization tasks, delegate to a subagent with a detailed spec rather than attempting inline. The user explicitly asked "把我所有的要求交给Claude做" — use `delegate_task` with a comprehensive requirements document for visualization work.

## Deployment

- URL: `https://nyx.hvh.expert/`
- Server: 小宝 (162.0.225.252)
- Nginx serves `/var/www/nyx/index.html` with Let's Encrypt SSL
- Cloudflare: DNS-only (grey cloud), no proxy
- Update: scp to server + `nginx -s reload`

## Verification

Always verify rendering via browser console:
```javascript
(function(){var $c=document.getElementById('c'),$ctx=$c.getContext('2d'),$d=$ctx.getImageData(0,0,$c.width,$c.height).data,$b=0;for(var $i=0;$i<$d.length;$i+=20){if($d[$i]+$d[$i+1]+$d[$i+2]>30)$b++}return 'W:'+$c.width+' H:'+$c.height+' bright:'+$b+'/'+Math.floor($d.length/20)})()
```
