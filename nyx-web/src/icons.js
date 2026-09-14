// SVG 图标库（内联，非 emoji）。stroke=currentColor 继承文字色。
export const icons = {
  brand: '<path d="M4 3h16v3H4zM6 6h12l-2 5h-8zM5 11h14l-1 2H6z"/><path d="M7 13h10l-1.2 3H8.2z"/><path d="M9 16h6l-.8 2h-4.4z"/><rect x="11" y="18" width="2" height="3" rx="1"/>',
  home: '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/><path d="M9 21v-6h6v6"/>',
  book: '<path d="M4 4h7a3 3 0 0 1 3 3v13a2 2 0 0 0-2-2H4z"/><path d="M20 4h-7a3 3 0 0 0-3 3v13a2 2 0 0 1 2-2h8z"/>',
  timeline: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3.5 2"/><circle cx="5" cy="5" r="1.2"/><circle cx="19" cy="6" r="1.2"/><circle cx="6" cy="19" r="1.2"/>',
  persona: '<path d="M12 3a4 4 0 0 1 4 4v1a4 4 0 0 1-8 0V7a4 4 0 0 1 4-4z"/><path d="M4 21v-1a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v1"/>',
  pen: '<path d="M12 19l7-7-3-3-7 7-1 4z"/><path d="M16 5l3 3"/><path d="M4 20l2-1"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.09a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.09a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
  memory: '<path d="M5 5h3v14H5z"/><path d="M10 5h2v14h-2z"/><path d="M14 5h2.5v14H14z"/><path d="M18.5 5H20v14h-1.5z"/>',
  spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4"/><circle cx="12" cy="12" r="2.2"/>',
  wave: '<path d="M2 12c2.5-4 5-4 7.5 0s5 4 7.5 0 3-2 5 0"/>',
  loop: '<path d="M4 12a8 8 0 0 1 14-5M20 12a8 8 0 0 1-14 5"/><path d="M18 3v4h-4M6 21v-4h4"/>',
  check: '<path d="M4 12.5 9 17.5 20 6.5"/>',
  arrowL: '<path d="M19 12H5M11 6l-6 6 6 6"/>',
  arrowR: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  dot: '<circle cx="12" cy="12" r="2.4"/>',
  seal: '<path d="M12 3 20 6.5V12c0 5-3.4 8.2-8 9-4.6-.8-8-4-8-9V6.5z"/><path d="M8.5 12.5l2.3 2.3L15.6 9.8"/>',
}

export function icon(name) {
  return icons[name] || icons.dot
}
