/* ──────────────────────────────────────────────────
   Varthaai Admin — Blog Editor + AI Writing Assistant
   Requires: jQuery, utils.js (apiPost/apiPostForm/showLoader/escHtml),
   alert-modal.js, marked.min.js + purify.min.js (Markdown rendering)
────────────────────────────────────────────────── */
(function () {
  var ctx = window.BLOG_EDITOR_CTX || {};
  var sessionId = null;
  var tags = [];
  try { tags = JSON.parse(document.getElementById('blogTagsHidden').value || '[]'); } catch (e) { tags = []; }
  if (!Array.isArray(tags)) tags = [];

  function renderMd(text) {
    var src = text || '';
    if (window.marked && window.DOMPurify) {
      return window.DOMPurify.sanitize(window.marked.parse(src, { breaks: true, gfm: true }), { ADD_ATTR: ['target'] });
    }
    return escHtml(src).replace(/\n/g, '<br>');
  }

  /* ── Live Markdown preview ── */
  var contentEl = document.getElementById('blogContent');
  var previewEl = document.getElementById('blogPreview');
  function renderPreview() { previewEl.innerHTML = renderMd(contentEl.value); }
  contentEl.addEventListener('input', renderPreview);
  renderPreview();

  /* ── Publish date toggle ── */
  document.getElementById('blogIsPublished').addEventListener('change', function () {
    document.getElementById('publishDateContainer').style.display = this.checked ? '' : 'none';
  });

  /* ── Tag chips ── */
  var tagInput = document.getElementById('blogTagInput');
  var tagChips = document.getElementById('blogTagChips');
  function renderTags() {
    document.getElementById('blogTagsHidden').value = JSON.stringify(tags);
    tagChips.innerHTML = tags.map(function (t, i) {
      return '<span class="ba-tag-chip">' + escHtml(t) + '<button type="button" data-i="' + i + '">&times;</button></span>';
    }).join('');
    tagChips.querySelectorAll('button').forEach(function (btn) {
      btn.addEventListener('click', function () {
        tags.splice(parseInt(this.dataset.i), 1);
        renderTags();
      });
    });
  }
  tagInput.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    var v = tagInput.value.trim();
    if (v && tags.indexOf(v) === -1) { tags.push(v); renderTags(); }
    tagInput.value = '';
  });
  renderTags();

  /* ── AI assistant session ── */
  function ensureSession() {
    if (sessionId) return Promise.resolve(sessionId);
    var flavorSel = document.getElementById('aiFlavorRefs');
    var flavorRefs = Array.prototype.map.call(flavorSel.selectedOptions, function (o) { return parseInt(o.value); });
    return apiPost(ctx.sessionsUrl, {
      blog_id: ctx.blogId || null,
      topic: document.getElementById('aiTopic').value.trim(),
      target_audience: document.getElementById('aiAudience').value.trim(),
      tone: document.getElementById('aiTone').value.trim(),
      word_count_target: parseInt(document.getElementById('aiWordCount').value) || null,
      flavor_refs: flavorRefs
    }).then(function (res) {
      if (!res.success) { showAlertModal(res.message || 'Could not start an AI session.', 'danger'); throw new Error('session'); }
      sessionId = res.data.id;
      return sessionId;
    });
  }

  var msgsEl = document.getElementById('aiMsgs');
  function appendMessage(role, text) {
    var div = document.createElement('div');
    div.className = 'ba-msg ' + role;
    div.innerHTML = '<div class="who">' + (role === 'admin' ? 'You' : 'Assistant') + '</div><div class="md body"></div>';
    msgsEl.appendChild(div);
    var bodyEl = div.querySelector('.body');
    if (text) bodyEl.textContent = text;
    msgsEl.scrollTop = msgsEl.scrollHeight;
    return bodyEl;
  }

  /* ── Parsing assistant output into form fields ──
     Each action's system prompt (marketing/ai.py) asks for a specific shape;
     these mirror that shape so "Insert" lands text in the right field
     instead of always dumping it into the main content textarea. */

  function setField(id, value) {
    if (value === undefined || value === null) return false;
    document.getElementById(id).value = value;
    return true;
  }

  function mergeTags(newTags) {
    var added = 0;
    newTags.forEach(function (t) {
      t = t.trim();
      if (!t) return;
      var exists = tags.some(function (existing) { return existing.toLowerCase() === t.toLowerCase(); });
      if (!exists) { tags.push(t); added++; }
    });
    if (added) renderTags();
    return added;
  }

  // "# Title\n\nBody...\n\n---\nExcerpt: ..." (see GENERATE prompt)
  function parseGeneratedDraft(text) {
    var lines = text.split('\n');
    var title = '';
    var titleIdx = lines.findIndex(function (l) { return /^#\s+\S/.test(l.trim()); });
    if (titleIdx !== -1) {
      title = lines[titleIdx].trim().replace(/^#\s+/, '').trim();
      lines.splice(titleIdx, 1);
    }
    var body = lines.join('\n');
    var excerpt = '';
    var m = body.match(/\n-{3,}\s*\n\s*\**Excerpt\**:?\s*([\s\S]*)$/i);
    if (m) {
      excerpt = m[1].trim();
      body = body.slice(0, m.index).trim();
    }
    return { title: title, excerpt: excerpt, body: body.trim() };
  }

  // "- Title: ...\n- Excerpt: ...\n- Meta Title: ...\n- Meta Description: ...\n- Tags: a, b, c"
  function parseMetaSuggestions(text) {
    var fields = {};
    var re = /^[-*\d.\s]*\**(title|excerpt|meta title|meta description|tags)\**\s*:\s*(.+)$/i;
    text.split('\n').forEach(function (line) {
      var m = line.trim().match(re);
      if (!m) return;
      var key = m[1].toLowerCase().replace(/\s+/g, '_');
      fields[key] = m[2].trim().replace(/^\*\*|\*\*$/g, '').trim();
    });
    return fields;
  }

  function addInsertButton(bodyEl, action, textGetter) {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn-sm btn-outline-success insert-btn';

    if (action === 'suggest_meta') {
      btn.innerHTML = '<i class="fas fa-arrow-left me-1"></i>Apply suggestions';
      btn.addEventListener('click', function () {
        var f = parseMetaSuggestions(textGetter());
        var filled = [];
        if (setField('blogTitle', f.title)) filled.push('title');
        if (setField('blogExcerpt', f.excerpt)) filled.push('excerpt');
        if (setField('blogMetaTitle', f.meta_title)) filled.push('meta title');
        if (setField('blogMetaDescription', f.meta_description)) filled.push('meta description');
        var addedTags = f.tags ? mergeTags(f.tags.split(',')) : 0;
        if (addedTags) filled.push(addedTags + ' tag' + (addedTags === 1 ? '' : 's'));
        showAlertModal(
          filled.length ? ('Applied: ' + filled.join(', ') + ' — review before saving.') : 'Could not parse a suggestion from the response.',
          filled.length ? 'success' : 'warning',
        );
      });
    } else if (action === 'generate') {
      btn.innerHTML = '<i class="fas fa-arrow-left me-1"></i>Insert into editor';
      btn.addEventListener('click', function () {
        var parsed = parseGeneratedDraft(textGetter());
        var filled = [];
        if (parsed.title && setField('blogTitle', parsed.title)) filled.push('title');
        if (parsed.excerpt && setField('blogExcerpt', parsed.excerpt)) filled.push('excerpt');
        contentEl.value = parsed.body || textGetter();
        filled.push('content');
        renderPreview();
        showAlertModal('Filled in: ' + filled.join(', ') + ' — review before saving.', 'success');
      });
    } else if (action === 'rewrite') {
      btn.innerHTML = '<i class="fas fa-arrow-left me-1"></i>Replace editor content';
      btn.addEventListener('click', function () {
        contentEl.value = textGetter();
        renderPreview();
        showAlertModal('Editor content replaced with the rewrite — review before saving.', 'success');
      });
    } else {
      btn.innerHTML = '<i class="fas fa-arrow-left me-1"></i>Insert into editor';
      btn.addEventListener('click', function () {
        var text = textGetter();
        contentEl.value = contentEl.value ? (contentEl.value + '\n\n' + text) : text;
        renderPreview();
        showAlertModal('Inserted into the editor — review before saving.', 'success');
      });
    }
    bodyEl.parentNode.appendChild(btn);
  }

  function getCsrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : '';
  }

  function runAssistant(action, adminMessage) {
    ensureSession().then(function (sid) {
      if (adminMessage) appendMessage('admin', adminMessage);
      var bodyEl = appendMessage('assistant', null);
      bodyEl.innerHTML = '<span class="typing"><span>●</span><span>●</span><span>●</span></span>';

      var full = '';

      function finish() {
        if (!full) bodyEl.innerHTML = renderMd('_(no response)_');
        // Land as editable text regardless of a clean finish or a dropped
        // connection — never leave the admin staring at a spinner or losing
        // whatever streamed so far.
        addInsertButton(bodyEl, action, function () { return full; });
      }

      fetch(ctx.streamUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
        body: JSON.stringify({
          session_id: sid, action: action, message: adminMessage || '',
          model_id: document.getElementById('aiModel').value,
          blog_content: contentEl.value
        })
      }).then(function (resp) {
        if (!resp.ok || !resp.body) throw new Error('Request failed (' + resp.status + ')');
        var reader = resp.body.getReader();
        var decoder = new TextDecoder();
        var buf = '';
        function pump() {
          return reader.read().then(function (r) {
            if (r.done) { finish(); return; }
            buf += decoder.decode(r.value, { stream: true });
            var parts = buf.split('\n\n');
            buf = parts.pop();
            parts.forEach(function (part) {
              var line = part.trim();
              if (line.indexOf('data:') !== 0) return;
              var payload = line.slice(5).trim();
              if (payload === '[DONE]') return;
              var obj;
              try { obj = JSON.parse(payload); } catch (e) { return; }
              if (obj.delta) { full += obj.delta; bodyEl.innerHTML = renderMd(full); msgsEl.scrollTop = msgsEl.scrollHeight; }
              if (obj.error) { full += (full ? '\n\n' : '') + '_[assistant error: ' + obj.error + ']_'; bodyEl.innerHTML = renderMd(full); }
            });
            return pump();
          });
        }
        return pump();
      }).catch(function (err) {
        full += (full ? '\n\n' : '') + '_[connection lost — ' + ((err && err.message) || 'stream interrupted') + ']_';
        bodyEl.innerHTML = renderMd(full);
        finish();
      });
    }).catch(function () { /* ensureSession already surfaced an alert */ });
  }

  document.querySelectorAll('[data-action]').forEach(function (btn) {
    btn.addEventListener('click', function () { runAssistant(btn.dataset.action, ''); });
  });

  document.getElementById('aiChatForm').addEventListener('submit', function (e) {
    e.preventDefault();
    var input = document.getElementById('aiChatInput');
    var msg = input.value.trim();
    if (!msg) return;
    input.value = '';
    runAssistant('chat', msg);
  });

  /* ── Save ── */
  document.getElementById('saveBlogBtn').addEventListener('click', function () {
    var form = document.getElementById('blogForm');
    if (!form.checkValidity()) { form.reportValidity(); return; }
    var fd = new FormData();
    var blogId = document.getElementById('blogId').value;
    fd.append('action', blogId ? 'update' : 'create');
    fd.append('id', blogId || '');
    fd.append('title', document.getElementById('blogTitle').value);
    fd.append('excerpt', document.getElementById('blogExcerpt').value);
    fd.append('content', document.getElementById('blogContent').value);
    fd.append('meta_title', document.getElementById('blogMetaTitle').value);
    fd.append('meta_description', document.getElementById('blogMetaDescription').value);
    fd.append('tags', document.getElementById('blogTagsHidden').value);
    fd.append('is_published', document.getElementById('blogIsPublished').checked ? 1 : 0);
    fd.append('published_at', document.getElementById('blogPublishedAt').value || '');
    if (sessionId) fd.append('session_id', sessionId);
    var imgEl = document.getElementById('blogFeaturedImage');
    if (imgEl && imgEl.files[0]) fd.append('featured_image', imgEl.files[0]);
    var removeImg = document.getElementById('blogRemoveImage');
    if (removeImg && removeImg.checked) fd.append('remove_featured_image', 1);
    showLoader();
    apiPostForm(ctx.saveUrl, fd)
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) { window.location.href = ctx.listUrl; }
      })
      .fail(function () { showAlertModal('Error saving blog.', 'danger'); })
      .always(hideLoader);
  });
})();
