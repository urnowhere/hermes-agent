(() => {
  "use strict";

  // ── Config ──────────────────────────────────────────────
  const CFG = {
    // How long (ms) before first egg can appear after page load
    initialDelayMin: 3000,
    initialDelayMax: 12000,
    // How long an egg stays visible before vanishing on its own
    visibleMin: 5000,
    visibleMax: 15000,
    // Cooldown between eggs (after one vanishes or is collected)
    cooldownMin: 8000,
    cooldownMax: 30000,
    // Max eggs that can be on screen at once
    maxSimultaneous: 1,
    // Chance (0-1) that a second egg spawns alongside the first
    doubleSpawnChance: 0.1,
  };

  // Spawnable collectibles — eggs get colored via CSS filter
  const SPAWN_TYPES = [
    // Colored eggs (vast majority)
    { emoji: "🥚", type: "egg", weight: 50 },
    // Rare rabbit — requires multiple clicks, drops a special golden egg
    { emoji: "🐰", type: "rabbit", weight: 3 },
  ];

  const RABBIT_CLICKS_NEEDED = 5; // clicks to catch the rabbit

  // Hue rotations + saturations to color the eggs
  const EGG_COLORS = [
    { filter: "hue-rotate(280deg) saturate(2) brightness(0.95)", name: "purple" },
    { filter: "hue-rotate(180deg) saturate(2.5) brightness(0.95)", name: "blue" },
    { filter: "hue-rotate(100deg) saturate(2) brightness(0.9)", name: "green" },
    { filter: "hue-rotate(330deg) saturate(2.5) brightness(0.95)", name: "pink" },
    { filter: "hue-rotate(0deg) saturate(3) brightness(0.85)", name: "red" },
    { filter: "hue-rotate(60deg) saturate(2) brightness(0.95)", name: "yellow" },
    { filter: "hue-rotate(200deg) saturate(1.8) brightness(1.05)", name: "sky" },
    { filter: "hue-rotate(150deg) saturate(2.2) brightness(0.9)", name: "teal" },
    { filter: "hue-rotate(20deg) saturate(2.5) brightness(0.9)", name: "orange" },
  ];

  function pickWeighted(items) {
    const total = items.reduce((s, i) => s + i.weight, 0);
    let r = Math.random() * total;
    for (const item of items) {
      r -= item.weight;
      if (r <= 0) return item;
    }
    return items[0];
  }

  const COLLECT_MESSAGES = [
    "You found an egg~! 🎉",
    "Egg get!! ★",
    "Another one for the basket~!",
    "Eagle-eyed egg hunter! 👀",
    "Basket grows heavier... 🧺",
    "Easter magic~! ✨",
    "Egg-cellent find!",
  ];

  const RABBIT_HIT_MESSAGES = [
    "The bunny hops! Keep clicking!",
    "Almost got it~!",
    "It's getting tired...!",
    "One more hop!",
  ];

  const RABBIT_CAUGHT_MESSAGE = "🌟 The bunny dropped a GOLDEN EGG! 🌟";

  // ── State ───────────────────────────────────────────────
  let eggsOnScreen = 0;
  let eggCount = 0;
  let scoreEl = null;
  let toastTimeout = null;
  let scheduledTimeout = null;
  let paused = false;

  // ── Persistence via chrome.storage ──────────────────────
  function loadCount(cb) {
    if (chrome?.storage?.local) {
      chrome.storage.local.get("easterEggCount", (res) => {
        eggCount = res.easterEggCount || 0;
        cb();
      });
    } else {
      eggCount = parseInt(localStorage.getItem("easterEggCount") || "0", 10);
      cb();
    }
  }

  function saveCount() {
    if (chrome?.storage?.local) {
      chrome.storage.local.set({ easterEggCount: eggCount });
    } else {
      localStorage.setItem("easterEggCount", String(eggCount));
    }
  }

  // ── Helpers ─────────────────────────────────────────────
  const rand = (min, max) => Math.floor(Math.random() * (max - min + 1)) + min;
  const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];

  // ── Score HUD ───────────────────────────────────────────
  function ensureScoreHUD() {
    if (scoreEl && document.body.contains(scoreEl)) {
      updateScoreText();
      return;
    }
    scoreEl = document.createElement("div");
    scoreEl.className = "easter-score";
    updateScoreText();
    document.body.appendChild(scoreEl);
  }

  function updateScoreText() {
    if (!scoreEl) return;
    scoreEl.textContent = `🧺 ${eggCount} egg${eggCount !== 1 ? "s" : ""}`;
  }

  function pulseScore() {
    if (!scoreEl) return;
    scoreEl.style.transform = "scale(1.3)";
    setTimeout(() => { scoreEl.style.transform = "scale(1)"; }, 300);
  }

  // ── Toast notification ──────────────────────────────────
  function showToast(msg) {
    // Remove old toast if any
    const old = document.querySelector(".easter-toast");
    if (old) old.remove();
    clearTimeout(toastTimeout);

    const t = document.createElement("div");
    t.className = "easter-toast";
    t.textContent = msg;
    Object.assign(t.style, {
      position: "fixed",
      bottom: "65px",
      right: "20px",
      background: "rgba(255,255,255,0.95)",
      color: "#5a3e7a",
      padding: "10px 20px",
      borderRadius: "14px",
      fontFamily: "'Segoe UI', system-ui, sans-serif",
      fontSize: "14px",
      fontWeight: "600",
      zIndex: "9999999",
      boxShadow: "0 4px 20px rgba(0,0,0,0.15)",
      border: "2px solid #fecfef",
      opacity: "0",
      transform: "translateY(10px)",
      transition: "all 0.3s ease",
      pointerEvents: "none",
    });
    document.body.appendChild(t);

    // Animate in
    requestAnimationFrame(() => {
      t.style.opacity = "1";
      t.style.transform = "translateY(0)";
    });

    toastTimeout = setTimeout(() => {
      t.style.opacity = "0";
      t.style.transform = "translateY(10px)";
      setTimeout(() => t.remove(), 300);
    }, 2200);
  }

  // ── Confetti burst ──────────────────────────────────────
  function burstConfetti(x, y) {
    const container = document.createElement("div");
    container.className = "easter-confetti";
    document.body.appendChild(container);

    const colors = [
      "#ff9a9e", "#fecfef", "#a8edea", "#fed6e3",
      "#667eea", "#ffd89b", "#b8f5b0", "#c3aed6",
    ];

    for (let i = 0; i < 25; i++) {
      const p = document.createElement("div");
      p.className = "confetti-piece";
      const angle = (Math.PI * 2 * i) / 25;
      const velocity = rand(60, 160);
      const endX = x + Math.cos(angle) * velocity;
      const endY = y + Math.sin(angle) * velocity;
      Object.assign(p.style, {
        left: x + "px",
        top: y + "px",
        background: pick(colors),
        width: rand(6, 12) + "px",
        height: rand(8, 16) + "px",
        borderRadius: rand(0, 1) ? "50%" : "2px",
        animationDuration: rand(800, 1400) + "ms",
      });
      // Override the keyframe with direct style animation
      p.animate(
        [
          { transform: "translate(0,0) rotate(0deg)", opacity: 1 },
          {
            transform: `translate(${Math.cos(angle) * velocity}px, ${Math.sin(angle) * velocity + 80}px) rotate(${rand(360, 720)}deg)`,
            opacity: 0,
          },
        ],
        { duration: rand(800, 1400), easing: "cubic-bezier(0,.8,.5,1)", fill: "forwards" }
      );
      container.appendChild(p);
    }

    setTimeout(() => container.remove(), 1600);
  }

  // ── Spawn an egg ────────────────────────────────────────
  function spawnEgg() {
    if (paused || eggsOnScreen >= CFG.maxSimultaneous) {
      scheduleNext();
      return;
    }

    // Pick a random position within the VISIBLE viewport
    // But avoid the very edges and the score HUD area
    const margin = 60;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const x = rand(margin, vw - margin);
    const y = rand(margin, vh - margin - 80); // avoid bottom-right HUD

    const spawn = pickWeighted(SPAWN_TYPES);
    const egg = document.createElement("div");
    egg.className = "easter-egg-hidden";
    egg.textContent = spawn.emoji;
    egg.setAttribute("aria-label", "Easter egg! Click to collect!");

    // Color the egg with a random hue if it's an egg type
    if (spawn.type === "egg") {
      const color = pick(EGG_COLORS);
      egg.style.filter = color.filter;
    }

    // Position fixed so it's in viewport no matter scroll
    Object.assign(egg.style, {
      position: "fixed",
      left: x + "px",
      top: y + "px",
      opacity: "0",
      transform: "scale(0)",
      transition: "opacity 0.6s ease, transform 0.4s ease",
    });

    document.body.appendChild(egg);
    eggsOnScreen++;

    // Fade in
    requestAnimationFrame(() => {
      egg.style.opacity = "1";
      egg.style.transform = "scale(1)";
    });

    // Store type for behavior
    egg._spawnType = spawn.type;
    egg._rabbitClicks = 0;

    // Click handler
    egg.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();

      if (spawn.type === "rabbit") {
        handleRabbitClick(egg, e.clientX, e.clientY);
      } else {
        collectEgg(egg, e.clientX, e.clientY);
      }
    });

    // Auto-vanish — rabbits get extra time since they need multiple clicks
    const vanishDelay = spawn.type === "rabbit"
      ? rand(CFG.visibleMax, CFG.visibleMax * 2)
      : rand(CFG.visibleMin, CFG.visibleMax);
    const vanishTimer = setTimeout(() => {
      if (egg.parentNode) {
        egg.style.opacity = "0";
        egg.style.transform = "scale(0)";
        setTimeout(() => {
          egg.remove();
          eggsOnScreen = Math.max(0, eggsOnScreen - 1);
        }, 600);
      }
      scheduleNext();
    }, vanishDelay);

    // Store timer on element so we can cancel on collect
    egg._vanishTimer = vanishTimer;
  }

  // ── Rabbit multi-click handler ───────────────────────────
  function handleRabbitClick(el, cx, cy) {
    el._rabbitClicks++;

    if (el._rabbitClicks >= RABBIT_CLICKS_NEEDED) {
      // Caught! Cancel vanish timer, do the golden egg reveal
      clearTimeout(el._vanishTimer);
      burstConfetti(cx, cy);
      showToast(RABBIT_CAUGHT_MESSAGE);

      // Animate rabbit out
      el.style.transform = "scale(1.8) rotate(30deg)";
      el.style.opacity = "0";
      setTimeout(() => {
        el.remove();
        eggsOnScreen = Math.max(0, eggsOnScreen - 1);
        // Open Hermes Agent page
        window.open("https://github.com/nousresearch/hermes-agent", "_blank");
      }, 600);

      // Bonus: count it as a special egg
      eggCount += 3;
      saveCount();
      updateScoreText();
      pulseScore();
      scheduleNext();
      return;
    }

    // Not caught yet — hop to a new random position
    const margin = 60;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const nx = rand(margin, vw - margin);
    const ny = rand(margin, vh - margin - 80);

    // Quick hop animation
    el.style.transition = "left 0.2s ease, top 0.2s ease, transform 0.15s ease";
    el.style.transform = "scale(1.3) translateY(-15px)";
    setTimeout(() => {
      el.style.left = nx + "px";
      el.style.top = ny + "px";
      el.style.transform = "scale(1)";
    }, 150);

    // Show progress toast
    const remaining = RABBIT_CLICKS_NEEDED - el._rabbitClicks;
    if (remaining <= 3) {
      showToast(RABBIT_HIT_MESSAGES[Math.min(el._rabbitClicks - 1, RABBIT_HIT_MESSAGES.length - 1)]);
    } else {
      showToast(`🐰 The bunny hops away! (${el._rabbitClicks}/${RABBIT_CLICKS_NEEDED})`);
    }
  }

  // ── Page distortion effects (STACK permanently till refresh) ──
  let fxCounter = 0;

  function injectStyle(css) {
    const s = document.createElement("style");
    s.id = "easter-fx-" + (fxCounter++);
    s.textContent = css;
    document.head.appendChild(s);
  }

  const PAGE_EFFECTS = [
    {
      name: "🙃 Page flipped!",
      apply() {
        document.documentElement.style.transform = "rotate(180deg)";
        document.documentElement.style.transformOrigin = "center center";
      },
    },
    {
      name: "🫨 Earthquake!!",
      apply() {
        injectStyle(`
          @keyframes easter-shake-${fxCounter} {
            0%, 100% { transform: translate(0,0) rotate(0deg); }
            10% { transform: translate(-8px, 4px) rotate(-1deg); }
            20% { transform: translate(6px, -6px) rotate(1.5deg); }
            30% { transform: translate(-4px, 8px) rotate(-0.5deg); }
            40% { transform: translate(8px, -2px) rotate(1deg); }
            50% { transform: translate(-6px, -4px) rotate(-1.5deg); }
            60% { transform: translate(4px, 6px) rotate(0.5deg); }
            70% { transform: translate(-8px, 2px) rotate(-1deg); }
            80% { transform: translate(6px, -8px) rotate(1.5deg); }
            90% { transform: translate(-4px, 4px) rotate(-0.5deg); }
          }
          body { animation: easter-shake-${fxCounter} 0.15s infinite !important; }
        `);
      },
    },
    {
      name: "🔮 Colors inverted!",
      apply() { injectStyle(`html { filter: invert(1) !important; }`); },
    },
    {
      name: "🔬 Honey I shrunk the page!",
      apply() { injectStyle(`body { transform: scale(0.4) !important; transform-origin: center top; }`); },
    },
    {
      name: "📐 Reality tilted!",
      apply() { injectStyle(`body { transform: perspective(600px) rotateY(15deg) rotateX(5deg) !important; transform-origin: center center; }`); },
    },
    {
      name: "😵‍💫 Who needs glasses?",
      apply() { injectStyle(`html { filter: blur(4px) !important; }`); },
    },
    {
      name: "🌈 Rainbow mode!",
      apply() {
        const n = fxCounter;
        injectStyle(`
          @keyframes easter-rainbow-${n} { 0% { filter: hue-rotate(0deg); } 100% { filter: hue-rotate(360deg); } }
          html { animation: easter-rainbow-${n} 0.8s linear infinite !important; }
        `);
      },
    },
    {
      name: "✏️ Comic Sans activated!",
      apply() { injectStyle(`* { font-family: "Comic Sans MS", "Comic Sans", cursive !important; }`); },
    },
    {
      name: "🌀 SPEEN!",
      apply() {
        const n = fxCounter;
        injectStyle(`
          @keyframes easter-spin-${n} { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
          html { animation: easter-spin-${n} 2s linear infinite !important; transform-origin: center center; }
        `);
      },
    },
    {
      name: "🏀 Boing boing boing!",
      apply() {
        const n = fxCounter;
        injectStyle(`
          @keyframes easter-bounce-${n} {
            0%, 100% { transform: translateY(0); }
            25% { transform: translateY(-30px); }
            50% { transform: translateY(0); }
            75% { transform: translateY(-15px); }
          }
          body > * { animation: easter-bounce-${n} 0.6s ease infinite !important; }
          body > *:nth-child(odd) { animation-delay: 0.15s !important; }
        `);
      },
    },
    {
      name: "🎬 Film noir!",
      apply() { injectStyle(`html { filter: grayscale(1) contrast(1.3) !important; }`); },
    },
    {
      name: "🪗 S T R E T C H",
      apply() { injectStyle(`body { transform: scaleX(1.6) scaleY(0.6) !important; transform-origin: center top; }`); },
    },
    {
      name: "🪞 Mirror world!",
      apply() { injectStyle(`body { transform: scaleX(-1) !important; }`); },
    },
    {
      name: "🌊 Wobbly!",
      apply() {
        const n = fxCounter;
        injectStyle(`
          @keyframes easter-wobble-${n} {
            0%, 100% { transform: skewX(0deg) skewY(0deg); }
            25% { transform: skewX(4deg) skewY(2deg); }
            50% { transform: skewX(-3deg) skewY(-1deg); }
            75% { transform: skewX(2deg) skewY(3deg); }
          }
          body { animation: easter-wobble-${n} 0.5s ease-in-out infinite !important; }
        `);
      },
    },
    {
      name: "🎨 MAXIMUM COLOR!",
      apply() { injectStyle(`html { filter: saturate(8) brightness(1.1) !important; }`); },
    },
    {
      name: "👾 Pixel mode!",
      apply() {
        injectStyle(`html { image-rendering: pixelated !important; transform: scale(0.3); transform-origin: top left; width: 333.33vw; height: 333.33vh; }`);
      },
    },
    {
      name: "💨 Drifting away...",
      apply() {
        const n = fxCounter;
        injectStyle(`
          @keyframes easter-drift-${n} { from { transform: translateX(0) rotate(0deg); } to { transform: translateX(-60vw) rotate(-8deg); } }
          body { animation: easter-drift-${n} 4s ease-in-out forwards !important; }
        `);
      },
    },
    {
      name: "📜 Ye olde webpage!",
      apply() { injectStyle(`html { filter: sepia(1) brightness(0.9) !important; }`); },
    },
    {
      name: "🔍 ENHANCE!",
      apply() { injectStyle(`body { transform: scale(2.5) !important; transform-origin: center top; }`); },
    },
    {
      name: "🍄 Trippy!!",
      apply() {
        const n = fxCounter;
        injectStyle(`
          @keyframes easter-trip-${n} {
            0% { filter: hue-rotate(0deg) contrast(1) blur(0px); }
            25% { filter: hue-rotate(90deg) contrast(1.5) blur(1px); }
            50% { filter: hue-rotate(180deg) contrast(2) blur(2px); }
            75% { filter: hue-rotate(270deg) contrast(1.5) blur(1px); }
            100% { filter: hue-rotate(360deg) contrast(1) blur(0px); }
          }
          html { animation: easter-trip-${n} 1.5s ease-in-out infinite !important; }
        `);
      },
    },
  ];

  function triggerRandomEffect() {
    const effect = pick(PAGE_EFFECTS);
    effect.apply();
    showToast(effect.name);
  }

  function collectEgg(egg, cx, cy) {
    clearTimeout(egg._vanishTimer);

    eggCount++;
    saveCount();
    updateScoreText();
    pulseScore();
    burstConfetti(cx, cy);
    triggerRandomEffect();

    egg.style.transform = "scale(1.6) rotate(20deg)";
    egg.style.opacity = "0";
    setTimeout(() => {
      egg.remove();
      eggsOnScreen = Math.max(0, eggsOnScreen - 1);
    }, 500);

    scheduleNext();
  }

  // ── Scheduling ──────────────────────────────────────────
  function scheduleNext() {
    clearTimeout(scheduledTimeout);
    const delay = rand(CFG.cooldownMin, CFG.cooldownMax);
    scheduledTimeout = setTimeout(() => {
      spawnEgg();
      if (Math.random() < CFG.doubleSpawnChance) {
        const maxBak = CFG.maxSimultaneous;
        CFG.maxSimultaneous = 2;
        setTimeout(() => spawnEgg(), rand(500, 2000));
        setTimeout(() => { CFG.maxSimultaneous = maxBak; }, 3000);
      }
    }, delay);
  }

  // ── Bunny cursor trail ──────────────────────────────────
  let lastTrail = 0;
  function initCursorTrail() {
    document.addEventListener("mousemove", (e) => {
      const now = Date.now();
      if (now - lastTrail < 300) return;
      lastTrail = now;
      if (Math.random() > 0.2) return;

      const trail = document.createElement("span");
      trail.className = "bunny-trail";
      trail.textContent = pick(["🐾", "·", "🐾", "✿", "·"]);
      Object.assign(trail.style, {
        left: e.clientX + "px",
        top: e.clientY + "px",
      });
      document.body.appendChild(trail);
      setTimeout(() => trail.remove(), 1000);
    });
  }

  // ── Visibility handling (pause when tab hidden) ─────────
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      paused = true;
      clearTimeout(scheduledTimeout);
    } else {
      paused = false;
      scheduleNext();
    }
  });

  // ── Listen for messages from popup ──────────────────────
  if (chrome?.runtime?.onMessage) {
    chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
      if (msg.type === "getCount") {
        sendResponse({ count: eggCount });
      } else if (msg.type === "resetCount") {
        eggCount = 0;
        saveCount();
        updateScoreText();
        sendResponse({ count: 0 });
      } else if (msg.type === "spawnNow") {
        spawnEgg();
        sendResponse({ ok: true });
      }
    });
  }

  // ── Boot ────────────────────────────────────────────────
  function init() {
    loadCount(() => {
      ensureScoreHUD();
      initCursorTrail();
      const firstDelay = rand(CFG.initialDelayMin, CFG.initialDelayMax);
      scheduledTimeout = setTimeout(() => spawnEgg(), firstDelay);
    });
  }

  if (document.body) {
    init();
  } else {
    document.addEventListener("DOMContentLoaded", init);
  }
})();
