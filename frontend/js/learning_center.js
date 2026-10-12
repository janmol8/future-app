const LearningCenter = (() => {
  const STORAGE_KEY = 'fa_learning_progress';
  const ONBOARDING_KEY = 'fa_onboarding_done';

  const lessons = [
    {
      id: 'chart-basics',
      title: 'Read a Candle Chart',
      level: 'Starter',
      description: 'Identify open, high, low, close, trend, and volatility on the live chart.',
      actionTab: null,
      actionLabel: 'Open Chart',
      task: 'Switch between Candles and Line, then mark this lesson complete.'
    },
    {
      id: 'indicators',
      title: 'Use Indicators Safely',
      level: 'Starter',
      description: 'Apply EMA or RSI and compare it with price action instead of treating it as a signal.',
      actionTab: 'indicators',
      actionLabel: 'Open Indicators',
      task: 'Add one indicator, observe false signals, then complete.'
    },
    {
      id: 'paper-trade',
      title: 'Place a Paper Trade',
      level: 'Practice',
      description: 'Use virtual money to place an order with a stop and a target.',
      actionTab: 'papertrade',
      actionLabel: 'Open Paper Trade',
      task: 'Place a simulated trade and record why you entered.'
    },
    {
      id: 'patterns',
      title: 'Choose One Pattern',
      level: 'Practice',
      description: 'Manually scan patterns and apply only the setup you want to study.',
      actionTab: 'patterns',
      actionLabel: 'Open Patterns',
      task: 'Refresh patterns, choose one, and review risk separately from chart drawings.'
    },
    {
      id: 'backtest',
      title: 'Backtest Before Belief',
      level: 'Advanced',
      description: 'Run a strategy test and inspect win rate, drawdown, and sample size.',
      actionTab: 'strategy',
      actionLabel: 'Open Strategy',
      task: 'Run a backtest and write one limitation of the result.'
    },
    {
      id: 'model-limits',
      title: 'Model Limitations',
      level: 'Advanced',
      description: 'Review model probabilities as classroom data, not as trading advice.',
      actionTab: 'ai',
      actionLabel: 'Open Model Lab',
      task: 'Refresh the model lab and note why a probability is not a prediction.'
    }
  ];

  function getProgress() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY)) || { completed: [], lastCompleted: null };
    } catch (e) {
      return { completed: [], lastCompleted: null };
    }
  }

  function saveProgress(progress) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(progress));
  }

  function init() {
    render();
    if (!localStorage.getItem(ONBOARDING_KEY)) {
      setTimeout(showOnboarding, 900);
    }
  }

  function render() {
    const root = document.getElementById('learning-center-root');
    if (!root) return;
    const progress = getProgress();
    const done = new Set(progress.completed || []);
    const completedCount = done.size;
    const pct = Math.round((completedCount / lessons.length) * 100);

    root.innerHTML = `
      <div class="learning-summary">
        <div>
          <div class="learning-kicker">Student Path</div>
          <h4>${completedCount}/${lessons.length} modules complete</h4>
          <p>Practice with virtual money only. Each module uses the tools already inside this simulator.</p>
        </div>
        <div class="learning-progress-ring" aria-label="${pct}% complete">${pct}%</div>
      </div>
      <div class="learning-progress-bar"><span style="width:${pct}%"></span></div>
      <div class="learning-actions">
        <button class="btn-secondary" onclick="LearningCenter.showOnboarding()">Restart Tour</button>
        <button class="btn-secondary" onclick="LearningCenter.resetProgress()">Reset Progress</button>
      </div>
      <div class="lesson-list">
        ${lessons.map(lesson => renderLesson(lesson, done.has(lesson.id))).join('')}
      </div>
    `;
  }

  function renderLesson(lesson, complete) {
    return `
      <article class="lesson-card ${complete ? 'complete' : ''}">
        <div class="lesson-topline">
          <span>${lesson.level}</span>
          <strong>${complete ? 'Complete' : 'Open'}</strong>
        </div>
        <h4>${lesson.title}</h4>
        <p>${lesson.description}</p>
        <div class="lesson-task">${lesson.task}</div>
        <div class="lesson-buttons">
          <button class="btn-secondary" onclick="LearningCenter.openTool('${lesson.actionTab || ''}')">${lesson.actionLabel}</button>
          <button class="btn-primary" onclick="LearningCenter.toggleComplete('${lesson.id}')">${complete ? 'Undo' : 'Mark Complete'}</button>
        </div>
      </article>
    `;
  }

  function openTool(tab) {
    if (!tab) {
      window.App?.closeInspector?.();
      return;
    }
    document.querySelector(`.sidebar-item[data-tab="${tab}"]`)?.click();
  }

  function toggleComplete(id) {
    const progress = getProgress();
    const set = new Set(progress.completed || []);
    if (set.has(id)) {
      set.delete(id);
    } else {
      set.add(id);
      progress.lastCompleted = new Date().toISOString();
      window.showNotification?.('Lesson progress saved', 'success');
    }
    progress.completed = Array.from(set);
    saveProgress(progress);
    render();
  }

  function resetProgress() {
    saveProgress({ completed: [], lastCompleted: null });
    render();
  }

  function showOnboarding() {
    const steps = [
      ['Chart', 'Start with chart reading. Change timeframe, compare candles and line view, and watch how price changes.'],
      ['Paper Trade', 'Use virtual money only. Practice entry, stop, target, and position size before thinking about real markets.'],
      ['Patterns', 'Patterns are manual now. Refresh them when you want, then choose what to draw so the chart stays clean.'],
      ['Model Lab', 'AI/model scores are for learning how features behave. They are not advice and can be wrong.'],
      ['Lessons', 'Use the Student Path to track modules, exercises, and progress.']
    ];
    let index = 0;

    const overlay = document.createElement('div');
    overlay.className = 'onboarding-overlay';
    overlay.innerHTML = `
      <div class="onboarding-card">
        <div class="learning-kicker">Welcome Tour</div>
        <h3 id="onboarding-title"></h3>
        <p id="onboarding-body"></p>
        <div class="onboarding-dots">${steps.map((_, i) => `<span data-dot="${i}"></span>`).join('')}</div>
        <div class="onboarding-actions">
          <button class="btn-secondary" id="onboarding-skip">Skip</button>
          <button class="btn-primary" id="onboarding-next">Next</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);

    const paint = () => {
      overlay.querySelector('#onboarding-title').textContent = steps[index][0];
      overlay.querySelector('#onboarding-body').textContent = steps[index][1];
      overlay.querySelectorAll('[data-dot]').forEach(dot => {
        dot.classList.toggle('active', Number(dot.dataset.dot) === index);
      });
      overlay.querySelector('#onboarding-next').textContent = index === steps.length - 1 ? 'Finish' : 'Next';
    };

    const finish = () => {
      localStorage.setItem(ONBOARDING_KEY, '1');
      overlay.remove();
    };

    overlay.querySelector('#onboarding-skip').onclick = finish;
    overlay.querySelector('#onboarding-next').onclick = () => {
      if (index >= steps.length - 1) finish();
      else {
        index += 1;
        paint();
      }
    };
    paint();
  }

  return { init, render, openTool, toggleComplete, resetProgress, showOnboarding };
})();

window.LearningCenter = LearningCenter;
