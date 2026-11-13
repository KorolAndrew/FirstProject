// CozyCode 1.3.0 - живая Яночка (SVG) с настроениями.
// Yanochka.mount(el) - нарисовать в элементе; Yanochka.mood(el, kind) - сменить настроение.
(function () {
  "use strict";
  const SVG = `
<svg class="yn" viewBox="0 0 240 240" xmlns="http://www.w3.org/2000/svg" data-mood="idle" role="img" aria-label="Яночка">
<style>
.yn .ln{stroke:#1b1416;stroke-width:5;stroke-linecap:round;stroke-linejoin:round;fill:none}
.yn .m{display:none}
.yn[data-mood=idle] .m-idle,.yn[data-mood=think] .m-think,.yn[data-mood=happy] .m-happy,.yn[data-mood=wow] .m-wow,
.yn[data-mood=sad] .m-sad,.yn[data-mood=sleep] .m-sleep,.yn[data-mood=love] .m-love,.yn[data-mood=eat] .m-eat{display:inline}
.yn[data-mood=idle] .m-open,.yn[data-mood=think] .m-open,.yn[data-mood=wow] .m-open{display:inline}
.yn .all{transform-origin:120px 230px;animation:yn-breath 4.2s ease-in-out infinite}
.yn[data-mood=happy] .all,.yn[data-mood=love] .all{animation:yn-bounce .9s ease-in-out infinite}
.yn[data-mood=sleep] .all{animation:yn-breath 6s ease-in-out infinite}
.yn[data-mood=sad] .all{animation:none;transform:translateY(3px)}
.yn .blink{transform-origin:120px 128px;animation:yn-blink 5.5s infinite}
.yn[data-mood=think] .pup{transform:translate(2px,-3px)}
.yn[data-mood=wow] .blink{transform:scale(1.1);animation:none}.yn[data-mood=sad] .brow,.yn[data-mood=wow] .brow{transform:translateY(-3px)}
.yn .tw{animation:yn-tw 1.4s ease-in-out infinite;transform-box:fill-box;transform-origin:center}
.yn .tw2{animation-delay:.5s}.yn .tw3{animation-delay:.9s}
.yn .dot{animation:yn-dot 1.2s infinite}.yn .d2{animation-delay:.2s}.yn .d3{animation-delay:.4s}
.yn .zz{animation:yn-zz 3s ease-in-out infinite}.yn .zz2{animation-delay:1.5s}
.yn .tear{animation:yn-tear 2.4s ease-in infinite}
.yn .heart{animation:yn-heart 1.6s ease-out infinite;transform-box:fill-box;transform-origin:center}
.yn .h2{animation-delay:.8s}
.yn .chew{animation:yn-chew .35s ease-in-out infinite;transform-box:fill-box;transform-origin:center}
@keyframes yn-breath{50%{transform:translateY(-3px) scale(1.01,.995)}}
@keyframes yn-bounce{0%,100%{transform:translateY(0)}40%{transform:translateY(-9px)}60%{transform:translateY(-9px)}}
@keyframes yn-blink{0%,92%,100%{transform:scaleY(1)}95%{transform:scaleY(.08)}}
@keyframes yn-tw{0%,100%{transform:scale(.5) rotate(0);opacity:.4}50%{transform:scale(1.15) rotate(20deg);opacity:1}}
@keyframes yn-dot{0%,100%{opacity:.25}50%{opacity:1}}
@keyframes yn-zz{0%{transform:translate(0,6px);opacity:0}30%{opacity:1}100%{transform:translate(14px,-22px);opacity:0}}
@keyframes yn-tear{0%{transform:translateY(0);opacity:0}20%{opacity:1}100%{transform:translateY(16px);opacity:0}}
@keyframes yn-heart{0%{transform:translateY(8px) scale(.4);opacity:0}30%{opacity:1}100%{transform:translateY(-26px) scale(1.1);opacity:0}}
@keyframes yn-chew{50%{transform:scaleY(.55)}}
@media (prefers-reduced-motion:reduce){.yn *{animation:none!important}}
</style>
<g class="all">
  <path d="M120.0 46.0 Q135.6 38.0 150.0 48.0 Q166.2 47.8 174.0 62.0 Q188.6 68.2 190.0 84.0 Q202.8 94.0 200.0 110.0 Q210.8 121.2 206.0 136.0 Q218.2 145.0 216.0 160.0 Q222.8 173.6 214.0 186.0 Q225.2 195.6 222.0 210.0 Q225.8 225.4 214.0 236.0 Q207.4 245.4 196.0 244.0 Q120.0 289.6 44.0 244.0 Q32.6 245.4 26.0 236.0 Q14.2 225.4 18.0 210.0 Q14.8 195.6 26.0 186.0 Q17.2 173.6 24.0 160.0 Q21.8 145.0 34.0 136.0 Q29.2 121.2 40.0 110.0 Q37.2 94.0 50.0 84.0 Q51.4 68.2 66.0 62.0 Q73.8 47.8 90.0 48.0 Q104.4 38.0 120.0 46.0 Z" fill="#fbf8f6" stroke="#2a1f26" stroke-width="3.2" stroke-linejoin="round"/>
  <!-- рожки барашка, закручены по бокам -->
  <g fill="none" stroke-linecap="round">
    <path d="M86 66C64 38 28 50 32 82C35 106 64 108 68 88C71 74 56 70 52 80" stroke="#2a1f26" stroke-width="15"/>
    <path d="M154 66C176 38 212 50 208 82C205 106 176 108 172 88C169 74 184 70 188 80" stroke="#2a1f26" stroke-width="15"/>
    <path d="M76 52C60 42 40 50 38 72M164 52C180 42 200 50 202 72" stroke="#6b5f66" stroke-width="2.6"/>
    <path d="M44 92q8 8 18 2M196 92q-8 8-18 2" stroke="#6b5f66" stroke-width="2"/>
  </g>
  <!-- шея и чёрная кофточка с колокольчиком -->
  <path d="M110 166h20v26h-20z" fill="#f6e3dc" stroke="#2a1f26" stroke-width="3"/>
  <path d="M52 244C58 210 84 194 108 190h24c24 4 50 20 56 54z" fill="#1d171b" stroke="#2a1f26" stroke-width="3" stroke-linejoin="round"/>
  <path d="M106 192q14 7 28 0" stroke="#3a3238" stroke-width="5" fill="none" stroke-linecap="round"/>
  <circle cx="120" cy="201" r="6.5" fill="#e9b949" stroke="#2a1f26" stroke-width="2.4"/>
  <path d="M116 203h8M120 203v4" stroke="#2a1f26" stroke-width="1.8" stroke-linecap="round"/>
  <!-- лицо -->
  <path d="M76 112C76 150 90 174 120 178C150 174 164 150 164 112C164 86 144 72 120 72C96 72 76 86 76 112Z" fill="#fff3ee" stroke="#2a1f26" stroke-width="3.2"/>
  <path d="M117 146q3 2 5 0" stroke="#c98d8d" stroke-width="2" fill="none" stroke-linecap="round"/>
  <!-- румянец со штрихами -->
  <g fill="#f4a3b8" opacity=".75"><ellipse cx="96" cy="148" rx="12" ry="6.5"/><ellipse cx="144" cy="148" rx="12" ry="6.5"/></g>
  <g stroke="#e0718f" stroke-width="1.6" stroke-linecap="round" opacity=".8">
    <path d="M89 151l4-6M95 152l4-6M101 151l3-5M137 151l4-6M143 152l4-6M149 151l3-5"/>
  </g>
  <!-- пряди и чёлка поверх -->
  <path d="M76.0 104.0 Q64.7 114.8 68.0 130.0 Q56.7 140.8 60.0 156.0 Q55.3 169.1 64.0 180.0 Q53.3 189.8 56.0 204.0 Q53.8 217.2 64.0 226.0 Q72.3 234.6 84.0 232.0 Q94.3 220.7 90.0 206.0 Q93.7 192.3 84.0 182.0 Q91.7 170.6 86.0 158.0 Q94.7 147.1 90.0 134.0 Q91.4 115.1 76.0 104.0 Z" fill="#fbf8f6" stroke="#2a1f26" stroke-width="3.2" stroke-linejoin="round"/>
  <path d="M164.0 104.0 Q175.3 114.8 172.0 130.0 Q183.3 140.8 180.0 156.0 Q184.7 169.1 176.0 180.0 Q186.7 189.8 184.0 204.0 Q186.2 217.2 176.0 226.0 Q167.7 234.6 156.0 232.0 Q145.7 220.7 150.0 206.0 Q146.3 192.3 156.0 182.0 Q148.3 170.6 154.0 158.0 Q145.3 147.1 150.0 134.0 Q148.6 115.1 164.0 104.0 Z" fill="#fbf8f6" stroke="#2a1f26" stroke-width="3.2" stroke-linejoin="round"/>
  <path d="M72.0 114.0 Q65.6 98.8 76.0 86.0 Q81.2 72.0 96.0 70.0 Q106.2 59.8 120.0 64.0 Q133.8 59.8 144.0 70.0 Q158.8 72.0 164.0 86.0 Q174.4 98.8 168.0 114.0 Q164.2 102.2 152.0 100.0 Q148.4 107.6 140.0 108.0 Q138.2 97.4 128.0 94.0 Q125.0 102.6 116.0 104.0 Q107.0 102.6 104.0 94.0 Q94.4 96.4 92.0 106.0 Q85.6 104.4 84.0 98.0 Q73.2 102.4 72.0 114.0 Z" fill="#fbf8f6" stroke="#2a1f26" stroke-width="3.2" stroke-linejoin="round"/>
  <g fill="none" stroke="#2a1f26" stroke-width="2.4" stroke-linecap="round">
    <path d="M100 80q-6 10 2 16M140 80q6 10-2 16M118 70q-4 10 4 14"/>
    <path d="M78 196q-6 6 0 12M162 196q6 6 0 12M36 170q-6 8 2 14M204 170q6 8-2 14"/>
  </g>
  <!-- хохолок -->
  <path d="M116 48C108 30 120 18 132 24C140 28 136 38 128 36" fill="none" stroke="#2a1f26" stroke-width="3.2" stroke-linecap="round"/>
  <!-- брови: чуть смущённые -->
  <g class="brow" fill="none" stroke="#2a1f26" stroke-width="2.6" stroke-linecap="round">
    <path d="M90 112q9-6 18-2M150 112q-9-6-18-2"/>
  </g>

  <!-- глаза: открытые (жду / думаю / ой!) -->
  <g class="m m-open"><g class="blink">
    <ellipse cx="100" cy="128" rx="9.5" ry="11.5" fill="#fff" stroke="#2a1f26" stroke-width="2"/>
    <ellipse cx="140" cy="128" rx="9.5" ry="11.5" fill="#fff" stroke="#2a1f26" stroke-width="2"/>
    <g class="pup">
      <ellipse cx="100" cy="130" rx="7.5" ry="9" fill="#5f93cf"/><ellipse cx="140" cy="130" rx="7.5" ry="9" fill="#5f93cf"/>
      <ellipse cx="100" cy="131" rx="4" ry="5" fill="#1d2a44"/><ellipse cx="140" cy="131" rx="4" ry="5" fill="#1d2a44"/>
      <circle cx="103" cy="126" r="2.6" fill="#fff"/><circle cx="143" cy="126" r="2.6" fill="#fff"/>
      <circle cx="97.5" cy="134" r="1.3" fill="#fff"/><circle cx="137.5" cy="134" r="1.3" fill="#fff"/>
    </g>
    <path d="M88 120q12-9 24 0M128 120q12-9 24 0" fill="none" stroke="#2a1f26" stroke-width="3.6" stroke-linecap="round"/>
    <path d="M88 120l-4-3M152 120l4-3" stroke="#2a1f26" stroke-width="2.4" stroke-linecap="round"/>
  </g></g>
  <!-- счастливые ^ ^ (готово, любовь, ем) -->
  <g class="m m-happy m-love m-eat" fill="none" stroke="#2a1f26" stroke-width="3.4" stroke-linecap="round">
    <path d="M90 132q10-12 20 0M130 132q10-12 20 0"/>
  </g>
  <!-- грустные -->
  <g class="m m-sad">
    <path d="M90 128q10 6 20 2M130 130q10 4 20-2" fill="none" stroke="#2a1f26" stroke-width="3.2" stroke-linecap="round"/>
    <path class="tear" d="M98 136q-4 7 0 10q4-3 0-10z" fill="#8db8e6"/>
  </g>
  <!-- спит -->
  <g class="m m-sleep" fill="none" stroke="#2a1f26" stroke-width="3.2" stroke-linecap="round">
    <path d="M90 130q10 7 20 0M130 130q10 7 20 0"/><path d="M90 130l-3 3M150 130l3 3" stroke-width="2"/>
  </g>

  <!-- ротик -->
  <path class="m m-idle m-think" d="M114 158q6 4 12 0" fill="none" stroke="#2a1f26" stroke-width="2.8" stroke-linecap="round"/>
  <path class="m m-happy m-love" d="M110 154q10 16 20 0z" fill="#d9566b" stroke="#2a1f26" stroke-width="2.8" stroke-linejoin="round"/>
  <ellipse class="m m-wow" cx="120" cy="159" rx="4.5" ry="6" fill="#d9566b" stroke="#2a1f26" stroke-width="2.8"/>
  <path class="m m-sad" d="M112 161q8-6 16 0" fill="none" stroke="#2a1f26" stroke-width="2.8" stroke-linecap="round"/>
  <ellipse class="m m-sleep" cx="120" cy="159" rx="2.8" ry="3.4" fill="#2a1f26"/>
  <g class="m m-eat"><ellipse class="chew" cx="120" cy="158" rx="6" ry="4.5" fill="#d9566b" stroke="#2a1f26" stroke-width="2.8"/></g>
</g>

<!-- значки настроения -->
<g class="m m-think" fill="#a59cab"><circle class="dot" cx="186" cy="38" r="5"/><circle class="dot d2" cx="202" cy="38" r="5"/><circle class="dot d3" cx="218" cy="38" r="5"/></g>
<g class="m m-happy" fill="#e9c46a">
  <path class="tw" d="M24 120l4 10 10 4-10 4-4 10-4-10-10-4 10-4z"/>
  <path class="tw tw2" d="M214 112l3 8 8 3-8 3-3 8-3-8-8-3 8-3z"/>
  <path class="tw tw3" d="M206 180l3 7 7 3-7 3-3 7-3-7-7-3 7-3z"/>
</g>
<g class="m m-wow"><path d="M210 30v26" stroke="#e9c46a" stroke-width="8" stroke-linecap="round"/><circle cx="210" cy="70" r="5" fill="#e9c46a"/></g>
<g class="m m-sleep" fill="#8db8e6" font-family="Segoe UI,sans-serif" font-weight="800">
  <text class="zz" x="184" y="56" font-size="22">z</text><text class="zz zz2" x="200" y="40" font-size="28">Z</text>
</g>
<g class="m m-love" fill="#f2a7c3">
  <path class="heart" d="M200 60c-6-8-18-2-12 8l12 12 12-12c6-10-6-16-12-8z"/>
  <path class="heart h2" d="M36 84c-5-6-14-1-9 6l9 9 9-9c5-7-4-12-9-6z"/>
</g>
</svg>`;

  // настроения CozyCode -> выражение лица
  const MAP = {
    idle: "idle", setup: "idle", thinking: "think", queued: "think", sent: "think", picked: "think",
    done: "happy", confirm: "wow", error: "sad", offline: "sad", signal_error: "sad",
    sleep: "sleep", love: "love", eat: "eat", happy: "happy", sad: "sad", think: "think", wow: "wow"
  };

  function night() { const h = new Date().getHours(); return h >= 23 || h < 7; }

  function mount(el) {
    if (!el || el.querySelector("svg.yn")) return;
    el.innerHTML = SVG;
    el.classList.add("yn-host");
  }

  function mood(el, kind) {
    if (!el) return;
    mount(el);
    let m = MAP[kind] || "idle";
    if (m === "idle" && night()) m = "sleep";  // ночью Яночка дремлет, пока ничего не происходит
    el.querySelector("svg.yn").setAttribute("data-mood", m);
  }

  window.Yanochka = {mount, mood, svg: SVG};
})();
