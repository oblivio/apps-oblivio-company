// Global State
let currentState = null;
let charts = {};
let canvases = {};
let currentBalanceStep = 0;
let quizChartInstance = null;

// --- Navigation ---
function navigate(pageId) {
    if (!COURSE_DATA.pages[pageId]) return;
    
    currentState = pageId;
    
    // Update Sidebar
    document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active-nav'));
    const navEl = document.getElementById(`nav-${pageId}`);
    if(navEl) navEl.classList.add('active-nav');

    // Render Content
    const page = COURSE_DATA.pages[pageId];
    const main = document.getElementById('main-content');
    
    // Fade out
    main.style.opacity = '0';
    
    setTimeout(() => {
        let html = `<header class="mb-8 animate-fadeIn">
            <h2 class="text-3xl font-bold text-slate-800">${page.title}</h2>
            ${page.subtitle ? `<p class="text-slate-600 mt-2">${page.subtitle}</p>` : ''}
        </header>`;

        if (page.content) {
            html += renderBlocks(page.content);
        }

        main.innerHTML = html;
        main.style.opacity = '1';

        // Re-run MathJax
        if (window.MathJax) MathJax.typesetPromise();

        // Initialize Interactive Components
        setTimeout(() => initInteractive(page.content), 50);
    }, 200);
}

// --- Content Renderer ---
function renderBlocks(blocks) {
    return blocks.map(block => {
        switch(block.type) {
            case 'hero':
                return `
                    <div class="max-w-4xl mx-auto text-center mb-10">
                        ${block.badge ? `<span class="bg-indigo-100 text-indigo-700 text-xs font-bold px-3 py-1 rounded-full uppercase tracking-wide mb-4 inline-block">${block.badge}</span>` : ''}
                        <h2 class="text-4xl md:text-5xl font-bold text-slate-800 mb-6">${block.title}</h2>
                        <p class="text-xl leading-relaxed text-slate-600 max-w-2xl mx-auto">${block.text}</p>
                    </div>`;
            
            case 'html':
                return `<div class="mb-8 animate-fadeIn">${block.html}</div>`;
            
            case 'grid':
            case 'card_grid':
            case 'cta_grid':
                const cols = block.columns || 2;
                const gridClass = cols === 3 ? 'md:grid-cols-3' : 'md:grid-cols-2';
                let items = block.content || block.cards || block.items;
                
                const gridContent = items.map(item => {
                    if (typeof item === 'string') return item;
                    // Handle cta_grid specific structure if needed, or generic card
                    return `
                        <div class="bg-white p-6 rounded-xl shadow-sm border border-slate-200 hover:shadow-md transition-all cursor-pointer group relative overflow-hidden" onclick="${item.link ? `navigate('${item.link}')` : ''}">
                            ${item.icon ? `<div class="absolute top-0 right-0 p-4 opacity-10 group-hover:opacity-20 transition-opacity"><i class="fas ${item.icon} text-6xl text-${item.color || 'indigo'}-500"></i></div>` : ''}
                            ${item.badge ? `<span class="text-xs font-bold text-${item.color || 'indigo'}-600 uppercase tracking-wider">${item.badge}</span>` : ''}
                            <h3 class="font-bold text-2xl text-slate-800 mt-1 mb-2">${item.title}</h3>
                            <p class="text-sm text-slate-600 relative z-10">${item.text}</p>
                        </div>
                    `;
                }).join('');
                return `<div class="grid grid-cols-1 ${gridClass} gap-6 mb-8">${gridContent}</div>`;

            case 'cta':
                return `
                    <div class="text-center mt-8 mb-12">
                         <button onclick="navigate('${block.link}')" class="bg-${block.color || 'slate'}-800 text-white font-bold py-4 px-8 rounded-xl shadow-lg hover:bg-${block.color || 'slate'}-900 transition-all text-lg flex items-center justify-center gap-2 mx-auto">
                            ${block.button_text} <i class="fas fa-arrow-right"></i>
                        </button>
                    </div>`;

            case 'links':
                return `
                    <div class="space-y-4 mb-8">
                        ${block.links.map(link => `
                            <a href="${link.url}" target="_blank" class="block bg-white p-4 rounded-xl shadow-sm border border-slate-200 hover:shadow-md transition-all flex items-center gap-4 group">
                                <div class="w-12 h-12 bg-slate-100 rounded-full flex items-center justify-center text-slate-500 group-hover:bg-red-50 group-hover:text-red-500 transition-colors">
                                    <i class="${link.icon || 'fas fa-link'} text-xl"></i>
                                </div>
                                <div>
                                    <h4 class="font-bold text-slate-800 group-hover:text-blue-600 transition-colors">${link.text}</h4>
                                    ${link.desc ? `<p class="text-sm text-slate-500">${link.desc}</p>` : ''}
                                </div>
                                <i class="fas fa-external-link-alt ml-auto text-slate-300"></i>
                            </a>
                        `).join('')}
                    </div>`;

            // New Renderers for Cheatsheet etc.
            case 'box':
                const boxColor = block.style === 'warning' ? 'orange' : block.style === 'info' ? 'sky' : 'slate';
                const boxBg = block.style === 'warning' ? 'bg-orange-50' : block.style === 'info' ? 'bg-sky-50' : 'bg-white';
                return `
                    <div class="${boxBg} p-6 rounded-lg border border-${boxColor}-200 shadow-sm mb-6">
                        ${block.title ? `<h3 class="font-bold text-lg mb-2 text-${boxColor}-900">${block.title}</h3>` : ''}
                        <div>${block.content}</div>
                    </div>`;

            case 'card':
                return `
                    <div class="bg-white rounded-lg border border-slate-200 overflow-hidden shadow-sm mb-6">
                        <div class="bg-${block.theme || 'indigo'}-50 px-4 py-2 font-bold text-${block.theme || 'indigo'}-800 text-sm flex justify-between items-center border-b border-${block.theme || 'indigo'}-100">
                            <span><i class="fas ${block.icon} mr-2"></i> ${block.title}</span>
                            ${block.badge ? `<span class="text-[10px] uppercase font-bold tracking-wider opacity-75 bg-white px-2 py-0.5 rounded text-${block.theme || 'indigo'}-600">${block.badge}</span>` : ''}
                        </div>
                        <div class="p-4 text-sm space-y-2 bg-white">
                            ${block.content}
                        </div>
                    </div>`;

            case 'list':
                return `
                    <div class="space-y-3 mb-6">
                        ${block.items.map(item => `
                            <div class="flex items-center justify-between p-3 bg-white border border-slate-200 rounded-lg shadow-sm">
                                <span class="text-sm font-medium text-slate-700">${item.label}</span>
                                <span class="text-xs font-bold bg-slate-100 text-slate-600 px-2 py-1 rounded border border-slate-200">${item.value}</span>
                            </div>
                        `).join('')}
                    </div>`;

            case 'strategy_list':
                return `
                    <div class="space-y-4 mb-8">
                        ${block.items.map(item => `
                            <div class="bg-white p-4 rounded-xl shadow-sm border border-slate-200 flex flex-col md:flex-row items-center gap-4">
                                <div class="bg-slate-100 p-3 rounded font-mono text-sm w-full md:w-48 text-center">${item.eq}</div>
                                <div class="flex-1">
                                    <h4 class="font-bold text-${item.color}-600">${item.title}</h4>
                                    <p class="text-xs text-slate-500">${item.desc}</p>
                                </div>
                            </div>
                        `).join('')}
                    </div>`;

            case 'mini_quiz_single':
                return `
                    <div class="mt-8 bg-gradient-to-r from-violet-50 to-pink-50 p-6 rounded-xl border border-slate-200 shadow-sm">
                        <h4 class="font-bold text-slate-800 text-sm mb-3 flex items-center"><i class="fas fa-question-circle mr-2 text-violet-600"></i> Quick Check</h4>
                        <p class="text-sm text-slate-600 mb-3">${block.question}</p>
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                            ${block.options.map((opt, i) => `
                                <button onclick="checkMiniSingle(this, ${opt.correct}, '${opt.feedback.replace(/'/g, "\\'")}')" class="mini-quiz-opt w-full text-left p-3 rounded border border-slate-200 text-sm font-semibold bg-white">${opt.text}</button>
                            `).join('')}
                        </div>
                        <div class="feedback hidden mt-3 text-sm font-bold p-3 rounded"></div>
                    </div>`;

            case 'video_analysis':
                return `
                    <div class="space-y-6">
                        ${block.videos.map(video => `
                            <div class="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden hover:shadow-md transition-shadow">
                                <div class="p-6 md:p-8 flex flex-col md:flex-row justify-between items-start md:items-center gap-6">
                                    <div class="flex-1">
                                        <div class="flex items-center gap-2 mb-2">
                                            <span class="bg-amber-100 text-amber-800 text-xs font-bold px-2 py-1 rounded uppercase">${video.tag || 'Video'}</span>
                                            <span class="text-stone-400 text-sm font-semibold">${video.author}</span>
                                        </div>
                                        <h3 class="text-xl font-bold text-stone-800 mb-2">${video.title}</h3>
                                        <p class="text-sm text-stone-600">${video.desc}</p>
                                    </div>
                                    <a href="${video.url}" target="_blank" class="w-full md:w-auto bg-red-600 hover:bg-red-700 text-white px-6 py-3 rounded-lg font-bold flex items-center justify-center shadow-sm transition-all transform active:scale-95">
                                        <i class="fab fa-youtube mr-2 text-xl"></i> WATCH VIDEO
                                    </a>
                                </div>
                                <div class="bg-stone-50 p-6 border-t border-stone-100">
                                    <details class="group">
                                        <summary class="flex items-center cursor-pointer text-stone-700 font-bold text-sm select-none hover:text-amber-600 transition-colors">
                                            <span class="w-6 h-6 bg-stone-200 rounded-full flex items-center justify-center mr-3 group-open:bg-amber-500 group-open:text-white transition-colors">
                                                <i class="fas fa-chevron-down group-open:rotate-180 transition-transform text-xs"></i>
                                            </span>
                                            Read Pedagogical Analysis
                                        </summary>
                                        <div class="mt-4 ml-9 text-stone-600 text-sm leading-relaxed">
                                            ${video.analysis}
                                        </div>
                                    </details>
                                </div>
                            </div>
                        `).join('')}
                    </div>
                `;

            case 'mastery_quiz':
                return `<div id="quiz-container" class="space-y-6"></div>
                        <button onclick="checkMasteryQuiz()" id="submit-btn" class="w-full bg-slate-800 hover:bg-slate-900 text-white font-bold py-4 rounded-xl shadow-lg mt-8 transition-colors">Check Answers</button>
                        <div id="quiz-results" class="hidden mt-8 p-6 bg-slate-100 rounded-xl border border-slate-200 text-center">
                            <h3 class="text-2xl font-bold text-slate-800">Results</h3>
                            <p id="score-display" class="text-4xl font-bold text-indigo-600 my-4">0%</p>
                            <div id="feedback-container" class="text-left space-y-4 mt-6"></div>
                        </div>`;
            
            case 'mastery_quiz_advanced':
                return `
                    <div id="quiz-container" class="bg-white rounded-xl shadow-lg border border-stone-200 p-6 md:p-10">
                        <!-- Questions will be injected here -->
                    </div>
                    <!-- Results Section -->
                    <div id="quiz-results" class="hidden mt-8 space-y-8 animate-fadeIn">
                        <div class="bg-stone-800 text-white p-8 rounded-xl shadow-lg text-center relative overflow-hidden">
                            <div class="absolute top-0 left-0 w-full h-2 bg-gradient-to-r from-amber-500 to-amber-700"></div>
                            <h3 class="text-3xl font-bold mb-2">Assessment Complete</h3>
                            <p class="text-stone-400 mb-8 uppercase tracking-widest text-xs">Performance Breakdown</p>
                            <div class="bg-white/10 rounded-xl p-6 max-w-sm mx-auto mb-6 backdrop-blur-sm border border-white/10">
                                <div class="relative w-full h-[200px] flex justify-center">
                                    <canvas id="scoreChart"></canvas>
                                </div>
                            </div>
                            <p id="score-text" class="text-3xl font-bold text-amber-400 font-mono"></p>
                        </div>
                        <div class="bg-white rounded-xl border border-stone-200 p-8 shadow-sm">
                            <h3 class="text-2xl font-bold text-stone-800 mb-8 border-b border-stone-100 pb-4 flex items-center">
                                <i class="fas fa-microscope text-amber-600 mr-3"></i> Exhaustive Solution Analysis
                            </h3>
                            <div id="solutions-container" class="space-y-6"></div>
                        </div>
                    </div>`;

            case 'mini_quiz':
                 return `<div class="bg-slate-50 p-6 rounded-xl border border-slate-200 my-8">
                            <h4 class="font-bold text-slate-700 mb-4"><i class="fas fa-question-circle mr-2"></i> Practice Question</h4>
                            <div id="mini-quiz-${block.level}"></div>
                            <button onclick="loadMiniQuiz(${block.level})" class="mt-4 text-sm font-bold text-blue-600 hover:underline">Load New Question</button>
                         </div>`;

            // Placeholders for interactive modules - content injected via initInteractive
            case 'interactive_graphing':
                return `<div class="bg-white p-4 rounded-xl border border-slate-200 shadow-sm h-[500px] relative"><canvas id="lineGraph"></canvas><div class="absolute bottom-4 left-4 right-4 bg-white/90 p-4 rounded-lg shadow border border-slate-200"><div class="flex gap-4"><div class="flex-1"><label class="text-xs font-bold block mb-1">m (Slope): <span id="m-val"></span></label><input type="range" id="slider-m" min="-5" max="5" step="0.5" value="1" oninput="updateGraph()"></div><div class="flex-1"><label class="text-xs font-bold block mb-1">b (Intercept): <span id="b-val"></span></label><input type="range" id="slider-b" min="-8" max="8" step="1" value="0" oninput="updateGraph()"></div></div></div></div>`;

            case 'interactive_slope_machine':
                return `<div class="bg-white p-8 rounded-xl border border-slate-200 shadow-sm text-center"><h3 class="font-bold text-lg mb-4">Slope Machine</h3><div class="flex flex-col md:flex-row gap-8 justify-center items-center"><div class="p-6 bg-slate-100 rounded-full w-32 h-32 flex items-center justify-center text-3xl font-mono font-bold" id="slope-display">2/3</div><div class="space-y-4"><div class="p-3 bg-sky-50 rounded border border-sky-100 text-sky-800 font-bold">Parallel: <span id="par-display">2/3</span></div><div class="p-3 bg-indigo-50 rounded border border-indigo-100 text-indigo-800 font-bold">Perpendicular: <span id="perp-display">-3/2</span></div></div></div><div class="mt-6 flex justify-center gap-2"><button onclick="setSlopeMachine(2,3)" class="px-3 py-1 bg-slate-200 rounded hover:bg-slate-300 font-bold text-sm">2/3</button><button onclick="setSlopeMachine(-4,1)" class="px-3 py-1 bg-slate-200 rounded hover:bg-slate-300 font-bold text-sm">-4</button><button onclick="setSlopeMachine(1,2)" class="px-3 py-1 bg-slate-200 rounded hover:bg-slate-300 font-bold text-sm">1/2</button></div></div>`;
            
            case 'interactive_visual_systems':
                 return `<div class="grid grid-cols-1 lg:grid-cols-3 gap-6 h-[500px]"><div class="lg:col-span-2 bg-white rounded-xl border border-slate-200 relative h-full"><canvas id="systemCanvas"></canvas><div id="sys-solution" class="absolute top-4 right-4 bg-white px-3 py-1 rounded shadow text-sm font-bold font-mono"></div></div><div class="space-y-4 overflow-y-auto"><div class="bg-sky-50 p-4 rounded-lg border-l-4 border-sky-500"><h4 class="font-bold text-sky-700 text-sm mb-2">Line A</h4><input type="range" id="m1" min="-4" max="4" step="0.5" value="1" oninput="updateSystem()"><br><input type="range" id="b1" min="-5" max="5" value="2" oninput="updateSystem()"></div><div class="bg-red-50 p-4 rounded-lg border-l-4 border-red-500"><h4 class="font-bold text-red-700 text-sm mb-2">Line B</h4><input type="range" id="m2" min="-4" max="4" step="0.5" value="-1" oninput="updateSystem()"><br><input type="range" id="b2" min="-5" max="5" value="-2" oninput="updateSystem()"></div></div></div>`;

            case 'interactive_inequalities':
                 return `<div class="h-[500px] relative bg-white rounded-xl border border-slate-200"><canvas id="inequalityCanvas"></canvas><div class="absolute bottom-4 left-4 right-4 bg-white/90 p-4 rounded shadow border border-slate-200"><div class="flex gap-2 mb-4 justify-center"><button onclick="setIneqSign('>')" class="px-3 py-1 border rounded hover:bg-orange-50 font-mono font-bold">&gt;</button><button onclick="setIneqSign('<')" class="px-3 py-1 border rounded hover:bg-orange-50 font-mono font-bold">&lt;</button><button onclick="setIneqSign('>=')" class="px-3 py-1 border rounded hover:bg-orange-50 font-mono font-bold">&ge;</button><button onclick="setIneqSign('<=')" class="px-3 py-1 border rounded hover:bg-orange-50 font-mono font-bold">&le;</button></div><div class="flex gap-4"><input type="range" id="ineq-m" min="-4" max="4" step="0.5" value="1" oninput="updateInequality()"><input type="range" id="ineq-b" min="-5" max="5" value="0" oninput="updateInequality()"></div></div></div>`;

            case 'interactive_balance_lab':
                return `
                    <div class="bg-white rounded-2xl shadow-lg border border-stone-200 overflow-hidden mb-10">
                        <div class="bg-stone-800 text-white p-5 flex justify-between items-center">
                            <h3 class="font-bold text-lg"><i class="fas fa-flask mr-2 text-amber-500"></i>Balance Lab</h3>
                            <span class="text-sm bg-stone-700 px-3 py-1 rounded-md font-mono tracking-wider border border-stone-600">3x + 4 = 5x - 6</span>
                        </div>
                        <div class="p-8 bg-stone-50">
                            <div class="flex justify-center items-end space-x-12 mb-8 h-64 relative border-b-4 border-stone-300 pb-4">
                                <!-- Left Pan -->
                                <div class="w-1/3 flex flex-col items-center z-10 relative group">
                                    <div class="absolute -top-10 text-stone-400 font-bold text-xs uppercase tracking-widest bg-white px-2 py-1 rounded border border-stone-200 shadow-sm">Left Pan</div>
                                    <div id="left-contents" class="flex flex-wrap justify-center items-end content-end w-full mb-1 min-h-[140px] pb-2 gap-1 transition-all duration-500 bg-white/50 rounded-lg border-2 border-transparent group-hover:border-stone-200/50"></div>
                                    <div class="w-full h-4 bg-stone-400 rounded-full shadow-inner"></div><div class="w-2 h-16 bg-stone-300"></div>
                                </div>
                                <!-- Fulcrum -->
                                <div class="absolute bottom-4 left-1/2 transform -translate-x-1/2 flex flex-col items-center">
                                    <div class="w-12 h-12 rounded-full bg-white border-4 border-stone-300 flex items-center justify-center font-bold text-2xl text-stone-400 shadow-sm z-20 mb-[-20px]">=</div>
                                    <div class="w-0 h-0 border-l-[24px] border-l-transparent border-r-[24px] border-r-transparent border-b-[48px] border-b-stone-700"></div>
                                </div>
                                <!-- Right Pan -->
                                <div class="w-1/3 flex flex-col items-center z-10 relative group">
                                    <div class="absolute -top-10 text-stone-400 font-bold text-xs uppercase tracking-widest bg-white px-2 py-1 rounded border border-stone-200 shadow-sm">Right Pan</div>
                                    <div id="right-contents" class="flex flex-wrap justify-center items-end content-end w-full mb-1 min-h-[140px] pb-2 gap-1 transition-all duration-500 bg-white/50 rounded-lg border-2 border-transparent group-hover:border-stone-200/50"></div>
                                    <div class="w-full h-4 bg-stone-400 rounded-full shadow-inner"></div><div class="w-2 h-16 bg-stone-300"></div>
                                </div>
                            </div>
                            <!-- Controls -->
                            <div class="bg-white p-6 rounded-xl border border-stone-200 shadow-sm">
                                <div class="flex flex-col md:flex-row justify-between items-start md:items-center mb-6 border-b border-stone-100 pb-4">
                                    <div>
                                        <p class="font-bold text-stone-700 text-lg">Current Equation: <span id="balance-equation-text" class="font-mono bg-amber-50 text-amber-800 px-3 py-1 rounded border border-amber-100">3x + 4 = 5x - 6</span></p>
                                        <p id="balance-instruction" class="text-sm text-stone-500 mt-2 font-medium">Goal: Isolate the bags (x).</p>
                                    </div>
                                    <button onclick="resetBalanceLab()" class="mt-4 md:mt-0 text-xs bg-stone-100 hover:bg-stone-200 px-4 py-2 rounded-lg font-bold text-stone-600 transition-colors flex items-center"><i class="fas fa-rotate-left mr-2"></i> Reset Lab</button>
                                </div>
                                <div id="balance-controls" class="grid grid-cols-1 md:grid-cols-3 gap-4"></div>
                                <div id="balance-feedback" class="mt-4 p-4 rounded-lg text-sm font-bold hidden border-l-4 transition-all"></div>
                            </div>
                        </div>
                    </div>`;

            case 'interactive_literal_eq':
                return `
                    <div class="bg-white rounded-2xl shadow-lg border border-stone-200 p-8 mb-10">
                        <div class="flex flex-col md:flex-row items-center justify-between mb-8">
                            <div class="w-full md:w-2/3">
                                <h3 class="text-xl font-bold text-stone-800 mb-1"><i class="fas fa-couch text-amber-500 mr-2"></i>Equation Rearrangement</h3>
                                <p class="text-sm text-stone-500">Analogy: Rearranging furniture without throwing anything out.</p>
                            </div>
                            <div class="w-full md:w-1/3 flex justify-end mt-4 md:mt-0">
                                <button onclick="setLiteralStep(0)" class="px-4 py-2 text-xs bg-stone-100 hover:bg-stone-200 rounded-lg font-bold text-stone-600 flex items-center transition-colors">
                                    <i class="fas fa-undo mr-2"></i> Restart
                                </button>
                            </div>
                        </div>
                        <!-- The Equation Stage -->
                        <div class="bg-stone-900 rounded-xl p-10 flex items-center justify-center min-h-[220px] relative mb-8 shadow-inner">
                            <div id="literal-display" class="text-3xl md:text-5xl font-mono font-bold text-white tracking-wider transition-all duration-700 flex items-center justify-center"></div>
                        </div>
                        <!-- Step Slider/Controls -->
                        <div class="relative pt-2 px-2">
                            <div class="flex justify-between mb-4 text-xs font-bold text-stone-400 uppercase tracking-widest">
                                <span>1. Start</span><span>2. Move X</span><span>3. Divide</span><span>4. Simplify</span>
                            </div>
                            <input type="range" min="0" max="3" value="0" id="literal-slider" 
                                class="w-full h-2 bg-stone-200 rounded-lg appearance-none cursor-pointer accent-amber-600 hover:accent-amber-500 transition-all"
                                oninput="setLiteralStep(this.value)">
                            <div id="literal-explanation" class="mt-6 p-5 bg-amber-50 border-l-4 border-amber-500 text-stone-800 rounded-r-lg shadow-sm"></div>
                        </div>
                    </div>`;

            default:
                return '';
        }
    }).join('');
}

// --- Interactive Initializers ---
function initInteractive(blocks) {
    blocks.forEach(block => {
        if(block.type === 'interactive_graphing') initGraph();
        if(block.type === 'interactive_visual_systems') initSystemCanvas();
        if(block.type === 'interactive_inequalities') initInequalityCanvas();
        if(block.type === 'mastery_quiz') renderMasteryQuiz();
        if(block.type === 'mastery_quiz_advanced') renderMasteryQuizAdvanced();
        if(block.type === 'mini_quiz') loadMiniQuiz(block.level);
        if(block.type === 'interactive_balance_lab') resetBalanceLab();
        if(block.type === 'interactive_literal_eq') setLiteralStep(0);
    });
}

// --- Specific Module Logic ---

// --- BALANCE LAB ---
const balanceStates = [
    {
        leftBags: 3, leftWeights: 4, rightBags: 5, rightWeights: -6,
        eq: "3x + 4 = 5x - 6",
        msg: "The scale is balanced. Notice variables on both sides.",
        options: [
            { text: "Subtract 3x from both sides", action: "next", hint: "Excellent. Always move the smaller variable first." },
            { text: "Subtract 5x from both sides", action: "wrong", hint: "Technically valid, but results in negative coefficients (-2x). Harder to solve." },
            { text: "Add 6 to both sides", action: "alt", hint: "Valid, but let's deal with the bags (variables) first." }
        ]
    },
    {
        leftBags: 0, leftWeights: 4, rightBags: 2, rightWeights: -6,
        eq: "4 = 2x - 6",
        msg: "Left bags removed. Now isolate the bags on the right.",
        options: [
            { text: "Add 6 to both sides", action: "next", hint: "Perfect. This neutralizes the -6 on the right." },
            { text: "Subtract 4 from both sides", action: "wrong", hint: "This moves numbers to the variable side. We want to separate them." }
        ]
    },
    {
        leftBags: 0, leftWeights: 10, rightBags: 2, rightWeights: 0,
        eq: "10 = 2x",
        msg: "10 units balance 2 bags. Final step.",
        options: [
            { text: "Divide by 2", action: "finish", hint: "Correct. We find the value of a single bag." },
            { text: "Subtract 2", action: "wrong", hint: "Division is the inverse of multiplication." }
        ]
    }
];

function renderBalanceLab() {
    if(!document.getElementById('left-contents')) return;
    const state = balanceStates[currentBalanceStep];
    const leftPan = document.getElementById('left-contents');
    const rightPan = document.getElementById('right-contents');
    const controls = document.getElementById('balance-controls');
    
    leftPan.innerHTML = '';
    rightPan.innerHTML = '';

    // Render visual elements
    for(let i=0; i<state.leftBags; i++) leftPan.innerHTML += `<div class="w-12 h-14 bg-amber-600 text-white flex items-center justify-center rounded-sm m-1 shadow">x</div>`;
    for(let i=0; i<Math.abs(state.leftWeights); i++) leftPan.innerHTML += state.leftWeights > 0 ? `<div class="w-8 h-8 bg-stone-600 text-white rounded-full flex items-center justify-center m-1 text-xs">1</div>` : `<div class="w-8 h-8 bg-red-500 text-white rounded-full flex items-center justify-center m-1 text-xs border border-white">-1</div>`;

    for(let i=0; i<state.rightBags; i++) rightPan.innerHTML += `<div class="w-12 h-14 bg-amber-600 text-white flex items-center justify-center rounded-sm m-1 shadow">x</div>`;
    for(let i=0; i<Math.abs(state.rightWeights); i++) rightPan.innerHTML += state.rightWeights > 0 ? `<div class="w-8 h-8 bg-stone-600 text-white rounded-full flex items-center justify-center m-1 text-xs">1</div>` : `<div class="w-8 h-8 bg-red-500 text-white rounded-full flex items-center justify-center m-1 text-xs border border-white">-1</div>`;

    document.getElementById('balance-equation-text').innerText = state.eq;
    document.getElementById('balance-instruction').innerText = state.msg;

    controls.innerHTML = '';
    if (currentBalanceStep < balanceStates.length) {
        state.options.forEach(opt => {
            const btn = document.createElement('button');
            btn.className = `p-4 border border-stone-200 rounded-lg text-left hover:bg-amber-50 hover:border-amber-300 transition-all group relative overflow-hidden shadow-sm`;
            btn.innerHTML = `<span class="font-bold text-stone-800 block relative z-10 text-sm md:text-base">${opt.text}</span>`;
            
            btn.onclick = () => {
                const feedback = document.getElementById('balance-feedback');
                feedback.innerText = opt.hint;
                feedback.classList.remove('hidden', 'bg-red-50', 'text-red-800', 'bg-green-50', 'text-green-800', 'border-red-500', 'border-green-500');
                
                if(opt.action === 'next' || opt.action === 'finish') {
                    feedback.classList.add('bg-green-50', 'text-green-800', 'border-green-500');
                    if(opt.action === 'next') {
                        setTimeout(() => {
                            currentBalanceStep++;
                            renderBalanceLab();
                        }, 1200);
                    } else {
                        leftPan.innerHTML = '<div class="w-16 h-16 text-xl font-bold bg-green-600 shadow-lg flex items-center justify-center rounded-full text-white">5</div>';
                        rightPan.innerHTML = '<div class="w-16 h-16 text-xl bg-green-600 shadow-lg flex items-center justify-center rounded-sm text-white">x</div>';
                        document.getElementById('balance-equation-text').innerText = "5 = x";
                        controls.innerHTML = `<div class="col-span-1 md:col-span-3 text-center p-6 bg-green-100 text-green-900 font-bold rounded-xl text-xl animate-pulse">SOLVED! x = 5</div>`;
                        document.getElementById('balance-instruction').innerText = "Balance Achieved.";
                    }
                } else {
                    feedback.classList.add('bg-red-50', 'text-red-800', 'border-red-500');
                }
                feedback.classList.remove('hidden');
            };
            controls.appendChild(btn);
        });
    }
}

function resetBalanceLab() {
    currentBalanceStep = 0;
    const feedback = document.getElementById('balance-feedback');
    if(feedback) feedback.classList.add('hidden');
    renderBalanceLab();
}

// --- LITERAL EQUATION LAB ---
const literalSteps = [
    { 
        eq: `2x + 3y = 9`, 
        expl: `<strong>Step 1: Locate the Target.</strong> We need $y$ by itself. The $2x$ and the $3$ are intruders.`
    },
    { 
        eq: `<span class="opacity-40">2x</span> + 3y = 9 <span class="text-amber-500 font-bold ml-2">- 2x</span>`, 
        expl: `<strong>Step 2: Move the Term.</strong> Subtract $2x$ from both sides. We shove it to the front of the right side to match $mx+b$.`
    },
    { 
        eq: `3y = <span class="text-amber-500 font-bold">-2x + 9</span>`, 
        expl: `<strong>Crucial Moment:</strong> Note that $9 - 2x$ does NOT become $7x$. They are unlike terms. The sofa is just on the other wall now.`
    },
    { 
        eq: `y = <div class="inline-block text-center align-middle mx-2"><div class="border-b-2 border-white px-2">-2x + 9</div><div>3</div></div>`, 
        expl: `<strong>Step 3: Divide.</strong> Divide the whole side by 3.`
    },
    { 
        eq: `y = <div class="fraction"><span class="numerator">-2</span><span class="denominator">3</span></div>x + 3`, 
        expl: `<strong>Final: Simplify.</strong> Use the "Heart Method" - divide $-2x$ by 3 AND $9$ by 3 separately.`
    }
];

function setLiteralStep(val) {
    if(!document.getElementById('literal-display')) return;
    val = parseInt(val);
    const data = literalSteps[val];
    document.getElementById('literal-display').innerHTML = data.eq;
    document.getElementById('literal-explanation').innerHTML = data.expl;
    const slider = document.getElementById('literal-slider');
    if(slider) slider.value = val;
}

// --- MASTERY QUIZ ADVANCED ---
function renderMasteryQuizAdvanced() {
    const container = document.getElementById('quiz-container');
    if(!container || !COURSE_DATA.quiz_data) return;
    
    let html = '';
    
    COURSE_DATA.quiz_data.forEach((q, idx) => {
        html += `
            <div class="mb-8 pb-6 border-b border-stone-100 last:border-0">
                <p class="font-bold text-lg text-stone-800 mb-4"><span class="text-amber-600 mr-2 font-mono">Q${idx+1}.</span> ${q.q}</p>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                    ${q.opts.map((opt, optIdx) => `
                        <label class="flex items-center space-x-3 p-4 rounded-lg border border-stone-200 hover:bg-stone-50 cursor-pointer transition-colors group">
                            <input type="radio" name="q${idx}" value="${optIdx}" class="text-amber-600 focus:ring-amber-500 w-5 h-5 accent-amber-600">
                            <span class="text-stone-700 group-hover:text-stone-900 font-medium">${opt}</span>
                        </label>
                    `).join('')}
                </div>
            </div>
        `;
    });

    html += `<button onclick="submitQuizAdvanced()" class="w-full bg-stone-800 hover:bg-stone-900 text-white font-bold py-4 rounded-xl shadow-lg transform active:scale-[0.99] transition-all flex items-center justify-center">
        <i class="fas fa-paper-plane mr-2"></i> Submit Assessment
    </button>`;
    
    container.innerHTML = html;
}

function submitQuizAdvanced() {
    let score = 0;
    const results = document.getElementById('quiz-results');
    const solutions = document.getElementById('solutions-container');
    const container = document.getElementById('quiz-container');

    solutions.innerHTML = '';

    COURSE_DATA.quiz_data.forEach((q, idx) => {
        const selected = document.querySelector(`input[name="q${idx}"]:checked`);
        const isCorrect = selected && parseInt(selected.value) === q.correct;
        
        if(isCorrect) score++;

        const solDiv = document.createElement('div');
        solDiv.className = `p-6 rounded-xl border-l-8 ${isCorrect ? 'border-green-500 bg-green-50/50' : 'border-red-500 bg-red-50/50'} shadow-sm`;
        solDiv.innerHTML = `
            <div class="flex items-center mb-3">
                <div class="w-8 h-8 rounded-full flex items-center justify-center ${isCorrect ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'} mr-3">
                    <i class="fas ${isCorrect ? 'fa-check' : 'fa-times'}"></i>
                </div>
                <span class="font-bold ${isCorrect ? 'text-green-800' : 'text-red-800'} text-lg">Question ${idx+1}</span>
            </div>
            ${q.analysis}
        `;
        solutions.appendChild(solDiv);
    });

    // Show Results
    results.classList.remove('hidden');
    container.classList.add('hidden');
    
    document.getElementById('score-text').innerText = `${Math.round((score/COURSE_DATA.quiz_data.length)*100)}% Mastery`;
    
    // Chart
    const ctx = document.getElementById('scoreChart').getContext('2d');
    if(quizChartInstance) quizChartInstance.destroy();
    
    quizChartInstance = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['Correct', 'Incorrect'],
            datasets: [{
                data: [score, COURSE_DATA.quiz_data.length - score],
                backgroundColor: ['#F59E0B', '#ffffff20'], 
                borderColor: '#ffffff',
                borderWidth: 2
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '75%',
            plugins: { legend: { display: false } }
        }
    });

    results.scrollIntoView({ behavior: 'smooth' });
}

// Graphing
function initGraph() {
    const canvas = document.getElementById('lineGraph');
    if(!canvas) return;
    canvas.width = canvas.parentElement.offsetWidth;
    canvas.height = canvas.parentElement.offsetHeight;
    updateGraph();
}
function updateGraph() {
    const ctx = document.getElementById('lineGraph').getContext('2d');
    const w = ctx.canvas.width; const h = ctx.canvas.height;
    const m = parseFloat(document.getElementById('slider-m').value);
    const b = parseInt(document.getElementById('slider-b').value);
    
    document.getElementById('m-val').innerText = m;
    document.getElementById('b-val').innerText = b;

    drawGrid(ctx, w, h);
    drawLine(ctx, m, b, w, h, '#0EA5E9');
}

// System
function initSystemCanvas() {
    const canvas = document.getElementById('systemCanvas');
    if(!canvas) return;
    canvas.width = canvas.parentElement.offsetWidth;
    canvas.height = canvas.parentElement.offsetHeight;
    updateSystem();
}
function updateSystem() {
    const ctx = document.getElementById('systemCanvas').getContext('2d');
    const w = ctx.canvas.width; const h = ctx.canvas.height;
    const m1 = parseFloat(document.getElementById('m1').value);
    const b1 = parseInt(document.getElementById('b1').value);
    const m2 = parseFloat(document.getElementById('m2').value);
    const b2 = parseInt(document.getElementById('b2').value);
    
    drawGrid(ctx, w, h);
    drawLine(ctx, m1, b1, w, h, '#0EA5E9');
    drawLine(ctx, m2, b2, w, h, '#EF4444');

    // Intersect
    if (m1 !== m2) {
        const x = (b2 - b1) / (m1 - m2);
        const y = m1 * x + b1;
        document.getElementById('sys-solution').innerText = `(${x.toFixed(1)}, ${y.toFixed(1)})`;
        drawPoint(ctx, x, y, w, h);
    } else {
        document.getElementById('sys-solution').innerText = b1 === b2 ? "Infinite" : "No Solution";
    }
}

// Inequalities
let ineqSign = '>';
function initInequalityCanvas() {
    const canvas = document.getElementById('inequalityCanvas');
    if(!canvas) return;
    canvas.width = canvas.parentElement.offsetWidth;
    canvas.height = canvas.parentElement.offsetHeight;
    updateInequality();
}
function setIneqSign(s) { ineqSign = s; updateInequality(); }
function updateInequality() {
    const ctx = document.getElementById('inequalityCanvas').getContext('2d');
    const w = ctx.canvas.width; const h = ctx.canvas.height;
    const m = parseFloat(document.getElementById('ineq-m').value);
    const b = parseInt(document.getElementById('ineq-b').value);
    
    drawGrid(ctx, w, h);
    // Shading would go here (simplified for now)
    drawLine(ctx, m, b, w, h, '#F97316', !ineqSign.includes('='));
}

// Shared Canvas Utils
function drawGrid(ctx, w, h) {
    ctx.clearRect(0,0,w,h);
    const scale = 30; const cx = w/2; const cy = h/2;
    ctx.strokeStyle = '#E2E8F0'; ctx.lineWidth = 1;
    // ... drawing loop ... (simplified)
    ctx.beginPath(); ctx.moveTo(cx,0); ctx.lineTo(cx,h); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(0,cy); ctx.lineTo(w,cy); ctx.stroke();
}
function drawLine(ctx, m, b, w, h, color, dashed) {
    const scale = 30; const cx = w/2; const cy = h/2;
    const x1 = -20; const y1 = m*x1+b;
    const x2 = 20; const y2 = m*x2+b;
    ctx.strokeStyle = color; ctx.lineWidth = 3;
    if(dashed) ctx.setLineDash([5,5]); else ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(cx + x1*scale, cy - y1*scale);
    ctx.lineTo(cx + x2*scale, cy - y2*scale);
    ctx.stroke();
    ctx.setLineDash([]);
}
function drawPoint(ctx, x, y, w, h) {
    const scale = 30; const cx = w/2; const cy = h/2;
    ctx.fillStyle = '#6366F1';
    ctx.beginPath(); ctx.arc(cx + x*scale, cy - y*scale, 6, 0, Math.PI*2); ctx.fill();
}

// Slope Machine
function setSlopeMachine(n, d) {
    const val = d === 1 ? n : `${n}/${d}`;
    document.getElementById('slope-display').innerText = val;
    document.getElementById('par-display').innerText = val;
    document.getElementById('perp-display').innerText = d === 1 ? `-1/${n}` : `${-d}/${n}`;
}

// Mini Quiz Single (Used in Systems module)
function checkMiniSingle(btn, isCorrect, feedbackText) {
    const container = btn.closest('.grid').parentElement;
    const feedbackBox = container.querySelector('.feedback');
    
    // Reset styles
    container.querySelectorAll('.mini-quiz-opt').forEach(b => {
        b.classList.remove('bg-green-100', 'text-green-800', 'bg-red-100', 'text-red-800', 'border-green-500', 'border-red-500');
        b.classList.add('bg-white');
    });

    if (isCorrect) {
        btn.classList.remove('bg-white');
        btn.classList.add('bg-green-100', 'text-green-800', 'border-green-500');
        feedbackBox.className = 'feedback mt-3 text-sm font-bold p-3 rounded bg-green-100 text-green-800 block animate-pulse';
        feedbackBox.innerHTML = '<i class="fas fa-check-circle mr-1"></i> ' + feedbackText;
    } else {
        btn.classList.remove('bg-white');
        btn.classList.add('bg-red-100', 'text-red-800', 'border-red-500');
        feedbackBox.className = 'feedback mt-3 text-sm font-bold p-3 rounded bg-red-100 text-red-800 block';
        feedbackBox.innerHTML = '<i class="fas fa-times-circle mr-1"></i> ' + feedbackText;
    }
}

// Quizzes
function renderMasteryQuiz() {
    const container = document.getElementById('quiz-container');
    if(!container || !COURSE_DATA.quiz_data) return;
    
    const html = COURSE_DATA.quiz_data.map((q, i) => `
        <div class="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
            <p class="font-bold text-lg text-slate-800 mb-4">${i+1}. ${q.question || q.q}</p>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                ${(q.options || q.opts).map((opt, oi) => {
                    const txt = typeof opt === 'string' ? opt : opt.text;
                    const val = typeof opt === 'string' ? oi : (opt.correct ? 'correct' : 'incorrect'); // simplified logic
                    return `<label class="flex items-center p-3 border border-slate-200 rounded hover:bg-slate-50 cursor-pointer">
                        <input type="radio" name="q${i}" value="${val}" class="mr-3">
                        <span class="text-sm">${txt}</span>
                    </label>`;
                }).join('')}
            </div>
        </div>
    `).join('');
    container.innerHTML = html;
}

function checkMasteryQuiz() {
    let score = 0;
    const total = COURSE_DATA.quiz_data.length;
    const resultsContainer = document.getElementById('quiz-results');
    const feedbackContainer = document.getElementById('feedback-container');
    if (feedbackContainer) feedbackContainer.innerHTML = '';

    COURSE_DATA.quiz_data.forEach((q, i) => {
        const selected = document.querySelector(`input[name="q${i}"]:checked`);
        let isCorrect = false;
        
        if (selected) {
            if (selected.value === 'correct') {
                isCorrect = true;
            } else {
                // It might be an index
                const selectedIndex = parseInt(selected.value);
                if (!isNaN(selectedIndex) && selectedIndex === q.correct) {
                    isCorrect = true;
                }
            }
        }

        if (isCorrect) score++;
        
        // Determine feedback text based on data structure
        let feedbackText = "";
        if (q.expl) {
            feedbackText = q.expl;
        } else if (q.options && typeof q.options[0] === 'object') {
             // Look for the correct option's feedback or the selected option's feedback
             const correctOpt = q.options.find(o => o.correct);
             feedbackText = correctOpt ? correctOpt.feedback : "";
        } else if (q.feedback) {
            feedbackText = q.feedback; // general feedback
        }

        if (feedbackContainer) {
            feedbackContainer.innerHTML += `
                <div class="p-4 mb-2 rounded border-l-4 ${isCorrect ? 'bg-green-50 border-green-500' : 'bg-red-50 border-red-500'}">
                    <p class="font-bold text-sm ${isCorrect ? 'text-green-800' : 'text-red-800'}">
                        Question ${i+1}: ${isCorrect ? 'Correct' : 'Incorrect'}
                    </p>
                    <p class="text-sm text-slate-600 mt-1">${feedbackText}</p>
                </div>
            `;
        }
    });

    const scoreDisplay = document.getElementById('score-display');
    if(scoreDisplay) scoreDisplay.innerText = `${Math.round((score/total)*100)}%`;
    
    if(resultsContainer) resultsContainer.classList.remove('hidden');
    const submitBtn = document.getElementById('submit-btn');
    if(submitBtn) submitBtn.classList.add('hidden');
}

function loadMiniQuiz(level) {
    const container = document.getElementById(`mini-quiz-${level}`);
    if(!container || !COURSE_DATA.quiz_data) return;
    
    // Filter by level if available
    const questions = COURSE_DATA.quiz_data.filter(q => !q.level || q.level == level);
    if(questions.length === 0) return;
    const q = questions[Math.floor(Math.random() * questions.length)];
    
    container.innerHTML = `
        <p class="font-bold mb-3">${q.question}</p>
        <div class="space-y-2">
            ${q.options.map(o => `<button class="w-full text-left p-2 border rounded hover:bg-slate-50 text-sm" onclick="this.classList.add( ${o.correct} ? 'bg-green-100' : 'bg-red-100')">${o.text}</button>`).join('')}
        </div>
    `;
}

// Init Page
document.addEventListener('DOMContentLoaded', () => {
    // Navigate to first page in list
    if(COURSE_DATA.navigation.length > 0) {
        navigate(COURSE_DATA.navigation[0].id);
    }

    // Global Resize Listener for Canvases
    window.addEventListener('resize', () => {
        // Debounce slightly if needed, but direct call is usually fine for these simple graphs
        if (document.getElementById('lineGraph')) initGraph();
        if (document.getElementById('systemCanvas')) initSystemCanvas();
        if (document.getElementById('inequalityCanvas')) initInequalityCanvas();
    });
});
