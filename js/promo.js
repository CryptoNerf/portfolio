/**
 * Промо-материалы веб-проектов: плитки и полноэкранный просмотр.
 *
 * Список материалов лежит в js/promo-data.js (его пишет tools/build-images.py
 * из папок promo-web/ и promo-project/). Элемент страницы привязывается к
 * проекту атрибутом data-promo="<папка проекта>". Название проекта — из
 * data-promo-name, а у строки таблицы — из её первой ячейки.
 *
 * Просмотр сквозной: дойдя до последней картинки проекта, стрелка → переходит
 * к первой картинке следующего проекта, поэтому все работы можно пролистать
 * подряд, не закрывая окно. ↑/↓ прыгают между проектами сразу.
 *
 * При закрытии на document отправляется событие promo:close с detail.project —
 * страница может прокрутить таблицу к проекту, на котором остановились.
 */
(function () {
  'use strict';

  var DATA = window.PROMO || {};

  // Пути в promo-data.js записаны от корня сайта, а страница лежит в html/.
  // Корень вычисляем из адреса самого скрипта, чтобы не зашивать "../".
  var ROOT = (function () {
    var script = document.currentScript;
    var src = script ? script.getAttribute('src') : '';
    return src.replace(/js\/promo\.js(\?.*)?$/, '');
  })();

  function url(path) {
    return encodeURI(ROOT + path);
  }

  function items(project) {
    return DATA[project] || [];
  }

  function plural(n, one, few, many) {
    var mod10 = n % 10;
    var mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return one;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
    return many;
  }

  function countLabel(n) {
    return n + ' ' + plural(n, 'материал', 'материала', 'материалов');
  }

  function duration(seconds) {
    var s = Math.max(0, Math.round(seconds || 0));
    return Math.floor(s / 60) + ':' + ('0' + (s % 60)).slice(-2);
  }

  // Название проекта. У строки таблицы — первый текст в первой ячейке:
  // после него страница может дописать в ячейку свою кнопку промо.
  function name(el) {
    if (el.hasAttribute('data-promo-name')) {
      return el.getAttribute('data-promo-name');
    }
    return el.cells[0].firstChild.textContent.trim();
  }

  // Проекты в порядке на странице — в этом порядке их листает просмотр.
  function projects() {
    var rows = document.querySelectorAll('[data-promo]');
    var list = [];
    for (var i = 0; i < rows.length; i += 1) {
      var key = rows[i].getAttribute('data-promo');
      if (items(key).length) {
        list.push({ key: key, name: name(rows[i]), row: rows[i] });
      }
    }
    return list;
  }

  /* ---------- плитка ---------- */

  // Плитка с подписью: под материалом — имя его файла, как в promo-web/.
  function tile(project, index) {
    var item = items(project)[index];
    var figure = document.createElement('figure');
    figure.className = 'promo-item';
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'promo-tile';
    button.style.aspectRatio = item.w + ' / ' + item.h;
    button.setAttribute('aria-label', 'Открыть ' + item.name);

    if (item.type === 'video') {
      var video = document.createElement('video');
      video.className = 'lazy-video';
      video.poster = url(item.poster);
      video.muted = true;
      video.loop = true;
      video.playsInline = true;
      video.preload = 'none';
      video.setAttribute('muted', '');
      var source = document.createElement('source');
      // В плитке — лёгкое превью без звука; полный ролик только в просмотре.
      source.setAttribute('data-src', url(item.preview || item.src));
      source.type = 'video/mp4';
      video.appendChild(source);
      button.appendChild(video);

      var badge = document.createElement('span');
      badge.className = 'promo-tile__badge';
      badge.textContent = '▶ ' + duration(item.d);
      button.appendChild(badge);
    } else {
      var img = document.createElement('img');
      img.className = 'progressive-img';
      img.src = url(item.low);
      img.setAttribute('data-src', url(item.thumb));
      img.width = item.w;
      img.height = item.h;
      img.decoding = 'async';
      img.alt = '';
      button.appendChild(img);
    }

    button.addEventListener('click', function () {
      open(project, index, button);
    });

    var caption = document.createElement('figcaption');
    caption.textContent = item.name;
    figure.appendChild(button);
    figure.appendChild(caption);
    return figure;
  }

  // Плитки проекта в контейнере, с ленивой загрузкой.
  function fill(container, project) {
    var list = items(project);
    for (var i = 0; i < list.length; i += 1) {
      container.appendChild(tile(project, i));
    }
    if (window.LazyMedia) {
      window.LazyMedia.observe(container);
    }
    return container;
  }

  /* ---------- полноэкранный просмотр ---------- */

  var box = null;
  var ui = {};
  var state = { order: [], p: 0, i: 0, opener: null };

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  function build() {
    box = el('div', 'promo-box');
    box.hidden = true;
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-label', 'Промо-материалы');

    var bar = el('div', 'promo-box__bar');
    ui.title = el('span', 'promo-box__title');
    ui.count = el('span', 'promo-box__count');
    ui.file = el('span', 'promo-box__file');
    ui.close = el('button', 'promo-box__btn', 'Закрыть [esc]');
    ui.close.type = 'button';
    bar.appendChild(ui.title);
    bar.appendChild(ui.count);
    bar.appendChild(ui.file);
    bar.appendChild(ui.close);

    var stage = el('div', 'promo-box__stage');
    ui.prev = el('button', 'promo-box__btn promo-box__nav', '←');
    ui.next = el('button', 'promo-box__btn promo-box__nav', '→');
    ui.prev.type = ui.next.type = 'button';
    ui.prev.setAttribute('aria-label', 'Предыдущий материал');
    ui.next.setAttribute('aria-label', 'Следующий материал');
    ui.media = el('div', 'promo-box__media');
    stage.appendChild(ui.prev);
    stage.appendChild(ui.media);
    stage.appendChild(ui.next);

    var foot = el('div', 'promo-box__foot');
    ui.prevProject = el('button', 'promo-box__btn');
    ui.nextProject = el('button', 'promo-box__btn');
    ui.prevProject.type = ui.nextProject.type = 'button';
    foot.appendChild(ui.prevProject);
    foot.appendChild(ui.nextProject);

    box.appendChild(bar);
    box.appendChild(stage);
    box.appendChild(foot);
    document.body.appendChild(box);

    ui.close.addEventListener('click', close);
    ui.prev.addEventListener('click', function () { step(-1); });
    ui.next.addEventListener('click', function () { step(1); });
    ui.prevProject.addEventListener('click', function () { jump(-1); });
    ui.nextProject.addEventListener('click', function () { jump(1); });
    document.addEventListener('keydown', onKey);

    // Свайпы: вбок — соседний материал, вверх/вниз — соседний проект.
    var start = null;
    stage.addEventListener('touchstart', function (e) {
      start = e.touches.length === 1 ? { x: e.touches[0].clientX, y: e.touches[0].clientY } : null;
    }, { passive: true });
    stage.addEventListener('touchend', function (e) {
      if (!start) return;
      var dx = e.changedTouches[0].clientX - start.x;
      var dy = e.changedTouches[0].clientY - start.y;
      start = null;
      if (Math.max(Math.abs(dx), Math.abs(dy)) < 50) return;
      if (Math.abs(dx) > Math.abs(dy)) step(dx < 0 ? 1 : -1);
      else jump(dy < 0 ? 1 : -1);
    });
  }

  function onKey(e) {
    if (!box || box.hidden) return;
    var keys = { ArrowLeft: function () { step(-1); }, ArrowRight: function () { step(1); },
                 ArrowUp: function () { jump(-1); }, ArrowDown: function () { jump(1); },
                 Escape: close };
    if (keys[e.key]) {
      e.preventDefault();
      keys[e.key]();
    }
  }

  function current() {
    return state.order[state.p];
  }

  function preload(p, i) {
    var project = state.order[p];
    var item = project && items(project.key)[i];
    if (item && item.type === 'img') {
      new Image().src = url(item.full);
    }
  }

  function render() {
    var project = current();
    var list = items(project.key);
    var item = list[state.i];
    var total = state.order.length;

    ui.title.textContent = project.name;
    ui.count.textContent = (state.i + 1) + ' / ' + list.length;
    ui.file.textContent = item.name;
    ui.prevProject.textContent = '↑ ' + state.order[(state.p - 1 + total) % total].name;
    ui.nextProject.textContent = state.order[(state.p + 1) % total].name + ' ↓';
    ui.prevProject.hidden = ui.nextProject.hidden = total < 2;

    ui.media.textContent = '';
    if (item.type === 'video') {
      var video = el('video');
      video.src = url(item.src);
      video.poster = url(item.poster);
      video.controls = true;
      video.autoplay = true;
      video.loop = true;
      video.playsInline = true;
      ui.media.appendChild(video);
    } else {
      // Миниатюра уже в кэше после ленты — показываем её сразу,
      // а полную версию подменяем, когда догрузится.
      var img = el('img');
      img.alt = project.name + ', ' + item.name;
      img.src = url(item.thumb);
      var full = new Image();
      full.onload = function () {
        if (img.isConnected) img.src = full.src;
      };
      full.src = url(item.full);
      ui.media.appendChild(img);
    }

    var next = state.i + 1 < list.length ? [state.p, state.i + 1] : [(state.p + 1) % total, 0];
    preload(next[0], next[1]);
    preload(state.p, state.i - 1);
  }

  function step(delta) {
    var total = state.order.length;
    var i = state.i + delta;
    if (i >= items(current().key).length) {
      state.p = (state.p + 1) % total;
      i = 0;
    } else if (i < 0) {
      state.p = (state.p - 1 + total) % total;
      i = items(current().key).length - 1;
    }
    state.i = i;
    render();
  }

  function jump(delta) {
    var total = state.order.length;
    state.p = (state.p + delta + total) % total;
    state.i = 0;
    render();
  }

  function open(project, index, opener) {
    if (!box) build();
    state.order = projects();
    state.p = 0;
    for (var p = 0; p < state.order.length; p += 1) {
      if (state.order[p].key === project) state.p = p;
    }
    state.i = index || 0;
    state.opener = opener || document.activeElement;
    box.hidden = false;
    document.documentElement.classList.add('promo-locked');
    render();
    ui.close.focus();
  }

  function close() {
    if (!box || box.hidden) return;
    var project = current().key;
    box.hidden = true;
    ui.media.textContent = '';
    document.documentElement.classList.remove('promo-locked');
    if (state.opener && state.opener.isConnected) {
      state.opener.focus({ preventScroll: true });
    }
    document.dispatchEvent(new CustomEvent('promo:close', { detail: { project: project } }));
  }

  window.Promo = {
    items: items,
    countLabel: countLabel,
    name: name,
    fill: fill,
    open: open
  };
})();
