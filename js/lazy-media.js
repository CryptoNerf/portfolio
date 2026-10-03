/**
 * Ленивая подгрузка картинок и видео.
 *
 * Раньше галерея стартовала так: на DOMContentLoaded скрипт создавал new Image()
 * сразу для всех работ и параллельно запускались все видео. Браузер получал
 * больше сотни мегабайт запросов одновременно, соединение захлёбывалось, и
 * часть картинок грузилась минутами или отваливалась по таймауту.
 *
 * Теперь тяжёлая версия запрашивается только когда элемент подходит к экрану,
 * и не больше MAX_PARALLEL штук за раз. До этого момента видно плейсхолдер
 * из source/img/low/ — он весит около килобайта и появляется мгновенно.
 *
 * Разметка:
 *   <img class="progressive-img"
 *        src="../source/img/low/ИМЯ.png"      плейсхолдер, грузится сразу
 *        data-src="../source/img/opt/ИМЯ.webp" что подставить вместо него
 *        data-fallback="../source/img/ИМЯ.png" запасной вариант, если webp не открылся
 *        width="1216" height="1802">           чтобы страница не прыгала
 *
 *   <video class="lazy-video" poster="..." muted loop playsinline preload="none">
 *     <source data-src="../source/img/opt/ИМЯ.mp4" type="video/mp4">
 *   </video>
 */
(function () {
  'use strict';

  // Сколько картинок тянем одновременно. Ограничение важнее, чем кажется:
  // без него запросы начинают душить друг друга и всё встаёт.
  var MAX_PARALLEL = 4;

  // За сколько до появления в окне начинать грузить, чтобы к моменту
  // прокрутки картинка уже была готова.
  var PRELOAD_MARGIN = '600px 0px';

  var queue = [];
  var active = 0;

  function pump() {
    while (active < MAX_PARALLEL && queue.length > 0) {
      active += 1;
      queue.shift()();
    }
  }

  function release() {
    active -= 1;
    pump();
  }

  function loadImage(img) {
    var src = img.getAttribute('data-src');
    if (!src || img.getAttribute('data-state')) {
      return;
    }
    img.setAttribute('data-state', 'loading');

    queue.push(function () {
      var probe = new Image();

      probe.onload = function () {
        img.src = src;
        img.classList.add('loaded');
        img.setAttribute('data-state', 'done');
        release();
      };

      probe.onerror = function () {
        // Не оставляем ячейку размытой навсегда: пробуем оригинал,
        // а если и он не открылся — просто снимаем размытие с плейсхолдера.
        var fallback = img.getAttribute('data-fallback');
        if (fallback && fallback !== src) {
          img.src = fallback;
        }
        img.classList.add('loaded');
        img.setAttribute('data-state', 'error');
        release();
      };

      probe.src = src;
    });

    pump();
  }

  function playVideo(video) {
    if (!video.getAttribute('data-state')) {
      video.setAttribute('data-state', 'ready');
      var sources = video.querySelectorAll('source[data-src]');
      for (var i = 0; i < sources.length; i += 1) {
        sources[i].src = sources[i].getAttribute('data-src');
      }
      video.load();
    }
    var started = video.play();
    if (started && started.catch) {
      // Автовоспроизведение могут запретить — это не повод ронять остальное.
      started.catch(function () {});
    }
  }

  var imageWatcher = null;
  var videoWatcher = null;

  function setup() {
    if (imageWatcher || !('IntersectionObserver' in window)) {
      return;
    }

    imageWatcher = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          imageWatcher.unobserve(entry.target);
          loadImage(entry.target);
        }
      });
    }, { rootMargin: PRELOAD_MARGIN });

    // Видео держим на паузе за пределами экрана: иначе несколько роликов
    // качаются и декодируются одновременно и отнимают канал у картинок.
    videoWatcher = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          playVideo(entry.target);
        } else if (entry.target.getAttribute('data-state')) {
          entry.target.pause();
        }
      });
    }, { rootMargin: '200px 0px' });
  }

  // Подхватить картинки и видео внутри root. Страница web достраивает ленты
  // промо уже после загрузки, поэтому вызывает это сама для новых блоков.
  function observe(root) {
    var images = root.querySelectorAll('img.progressive-img[data-src]');
    var videos = root.querySelectorAll('video.lazy-video');
    var i;

    setup();

    // Старые браузеры без IntersectionObserver: грузим всё, но по очереди,
    // чтобы хотя бы не устраивать лавину запросов.
    if (!imageWatcher) {
      for (i = 0; i < images.length; i += 1) {
        loadImage(images[i]);
      }
      for (i = 0; i < videos.length; i += 1) {
        playVideo(videos[i]);
      }
      return;
    }

    for (i = 0; i < images.length; i += 1) {
      imageWatcher.observe(images[i]);
    }
    for (i = 0; i < videos.length; i += 1) {
      videoWatcher.observe(videos[i]);
    }
  }

  function init() {
    observe(document);
  }

  window.LazyMedia = { observe: observe };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
