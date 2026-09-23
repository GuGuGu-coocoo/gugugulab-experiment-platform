/* GEP v2 accounts page: bounded permission drafts, scoped bulk selection and
 * the unified password-confirmation dialog.
 *
 * Safety rules implemented here (the server remains the only authority):
 * - the dialog asks for a password only; Cancel and Escape send no request;
 * - an in-flight submit owns the dialog until its result is known: Escape, a
 *   close or a re-open can never unlock it, and a late response of an older
 *   request is never applied to a newer one;
 * - an unknown network result (fetch failure or a response body that cannot be
 *   read) is never reported as success: the client asks the read-only preview
 *   status of its own preview (`?preview_status=`), and only a consumed preview
 *   with a stored result counts as executed; a pending answer means "not
 *   finished at query time", never "the original request did not run";
 * - drafts live in this tab's sessionStorage, carry no secret, are scoped to the
 *   instance plus the actor's stable subject, and keep the governance revision
 *   they were written at: a revision change keeps the batch and marks it as
 *   needing a fresh preview instead of silently applying or dropping it; a
 *   revoked (read-only) entry can never be restored to the UI or submitted.
 *
 * Every user-visible string comes from server-rendered data-* attributes, so the
 * same script serves both languages and all three themes.
 */
(function () {
  'use strict';

  var dialog = document.querySelector('[data-confirm-dialog]');
  var matrix = document.querySelector('[data-permission-matrix][data-matrix-version="2"]');

  function each(list, fn) { Array.prototype.slice.call(list).forEach(fn); }

  // ---------------------------------------------------------------- drafts
  var DRAFT_PREFIX = 'gep.matrix.draft.';
  var COLLAPSE_PREFIX = 'gep.matrix.collapsed.';
  var revision = matrix ? (matrix.dataset.instanceRevision || '') : '';
  // Drafts are scoped to this instance and this actor's stable subject, so the
  // same tab can switch accounts or instances without reading a previous
  // person's drafts.
  var scope = matrix ? (matrix.dataset.draftScope || '') : '';

  function draftKey() { return DRAFT_PREFIX + scope; }

  function readDrafts() {
    if (!matrix || !scope) { return {}; }
    try {
      var raw = window.sessionStorage.getItem(draftKey());
      return raw ? JSON.parse(raw) : {};
    } catch (error) {
      return {};
    }
  }

  function writeDrafts(drafts) {
    if (!matrix || !scope) { return; }
    try {
      if (Object.keys(drafts).length) {
        window.sessionStorage.setItem(draftKey(), JSON.stringify(drafts));
      } else {
        window.sessionStorage.removeItem(draftKey());
      }
    } catch (error) { /* private mode: drafts simply do not persist */ }
  }

  function draftIsStale(draft) {
    return !draft || (draft.revision || '') !== revision;
  }

  function lockedEntries() {
    var locked = {};
    each(document.querySelectorAll('[data-locked-entry]'), function (el) {
      locked[el.dataset.lockedEntry] = true;
    });
    return locked;
  }

  function entryForms(accountId) {
    var result = [];
    each(document.querySelectorAll('form[data-matrix-entry]'), function (form) {
      if (!accountId || form.dataset.matrixEntry.indexOf(accountId + '-') === 0) { result.push(form); }
    });
    return result;
  }

  function formsFor(accountId) { return entryForms(accountId); }

  function entryValue(form) {
    var value = {visibility: null, actions: {}};
    var visibility = form.querySelector('[name=visibility]');
    if (visibility) { value.visibility = visibility.checked; }
    each(form.querySelectorAll('input[name^="action:"]'), function (box) {
      value.actions[box.name.slice('action:'.length)] = box.checked;
    });
    return value;
  }

  function changedFields(form) {
    var value = entryValue(form);
    var changed = 0;
    var visibility = form.querySelector('[name=visibility]');
    if (visibility && (visibility.dataset.stored === '1') !== value.visibility) { changed += 1; }
    Object.keys(value.actions).forEach(function (action) {
      var box = form.querySelector('input[name="action:' + action + '"]');
      if ((box.dataset.stored === '1') !== value.actions[action]) { changed += 1; }
    });
    return changed;
  }

  function editableChildBoxes(accountId) {
    var boxes = [];
    formsFor(accountId).forEach(function (form) {
      var visibility = form.querySelector('[name=visibility]');
      // A study without explicit visibility is skipped in the account-wide
      // scope: a bulk select must never create the contradictory
      // "invisible but child actions" submission the server refuses.
      if (!visibility || !visibility.checked) { return; }
      each(form.querySelectorAll('input[data-child="1"]'), function (box) { boxes.push(box); });
    });
    return boxes;
  }

  function refreshCounts(editedKey) {
    if (!matrix) { return; }
    var drafts = readDrafts();
    each(document.querySelectorAll('form[data-matrix-entry]'), function (form) {
      var key = form.dataset.matrixEntry;
      var changed = changedFields(form);
      var out = document.querySelector('[data-entry-state="' + key + '"] [data-change-count]');
      if (out) { out.textContent = String(changed); }
      if (changed) {
        var value = entryValue(form);
        // An entry keeps the revision its draft was written at unless the user
        // edited this entry in the current view; a stale draft is never
        // silently upgraded to the current revision.
        var base = (editedKey === key || !drafts[key]) ? revision : (drafts[key].revision || revision);
        drafts[key] = {visibility: value.visibility, actions: value.actions, revision: base};
        form.setAttribute('data-draft', '1');
        if (base !== revision) { form.setAttribute('data-draft-stale', '1'); }
        else { form.removeAttribute('data-draft-stale'); }
      } else {
        delete drafts[key];
        form.removeAttribute('data-draft');
        form.removeAttribute('data-draft-stale');
      }
    });
    writeDrafts(drafts);
    each(document.querySelectorAll('[data-matrix-user-row]'), function (row) {
      var id = row.dataset.matrixUserRow;
      var changed = formsFor(id).reduce(function (sum, form) { return sum + changedFields(form); }, 0);
      var out = document.querySelector('[data-scope-state="' + id + '"] [data-change-count]');
      if (out) { out.textContent = String(changed); }
      var selectAll = document.querySelector('[data-select-all-user="' + id + '"]');
      if (selectAll) {
        var boxes = editableChildBoxes(id);
        selectAll.checked = boxes.length > 0 && boxes.every(function (box) { return box.checked; });
      }
    });
    var banner = document.querySelector('[data-draft-banner]');
    if (banner) {
      var pending = Object.keys(drafts).length;
      banner.hidden = pending === 0;
      var count = banner.querySelector('[data-draft-count]');
      if (count) { count.textContent = String(pending); }
      var staleCount = Object.keys(drafts).filter(function (key) {
        return draftIsStale(drafts[key]);
      }).length;
      var staleOut = banner.querySelector('[data-draft-stale]');
      if (staleOut) {
        staleOut.hidden = staleCount === 0;
        var staleNumber = staleOut.querySelector('[data-draft-stale-count]');
        if (staleNumber) { staleNumber.textContent = String(staleCount); }
      }
    }
  }

  function applyDrafts() {
    var drafts = readDrafts();
    var locked = lockedEntries();
    // A revoked/read-only entry can never be restored to the UI or submitted;
    // its draft is dropped instead of staying a pending change.
    Object.keys(drafts).forEach(function (key) {
      if (locked[key]) { delete drafts[key]; }
    });
    each(document.querySelectorAll('form[data-matrix-entry]'), function (form) {
      var draft = drafts[form.dataset.matrixEntry];
      if (!draft) { return; }
      var visibility = form.querySelector('[name=visibility]');
      if (visibility && draft.visibility !== null && draft.visibility !== undefined) {
        visibility.checked = !!draft.visibility;
      }
      Object.keys(draft.actions || {}).forEach(function (action) {
        var box = form.querySelector('input[name="action:' + action + '"]');
        if (box && box.dataset.child === '1') { box.checked = !!draft.actions[action]; }
      });
      form.setAttribute('data-draft', '1');
      if (draftIsStale(draft)) { form.setAttribute('data-draft-stale', '1'); }
    });
    writeDrafts(drafts);
  }

  // -------------------------------------------------------- collapse toggles
  var collapsed = {};
  try {
    collapsed = JSON.parse(window.sessionStorage.getItem(COLLAPSE_PREFIX + revision) || '{}');
  } catch (error) { collapsed = {}; }

  function saveCollapsed() {
    try {
      var open = Object.keys(collapsed).filter(function (key) { return collapsed[key]; });
      if (open.length) {
        window.sessionStorage.setItem(COLLAPSE_PREFIX + revision, JSON.stringify(collapsed));
      } else {
        window.sessionStorage.removeItem(COLLAPSE_PREFIX + revision);
      }
    } catch (error) { /* ignore */ }
  }

  function setPanel(accountId, open) {
    var panel = document.querySelector('[data-matrix-studies="' + accountId + '"]');
    var toggle = document.querySelector('[data-matrix-toggle="' + accountId + '"]');
    if (panel) { panel.hidden = !open; }
    if (toggle) { toggle.setAttribute('aria-expanded', open ? 'true' : 'false'); }
  }

  each(document.querySelectorAll('[data-matrix-toggle]'), function (toggle) {
    var accountId = toggle.dataset.matrixToggle;
    setPanel(accountId, !collapsed[accountId]);
    toggle.addEventListener('click', function () {
      var panel = document.querySelector('[data-matrix-studies="' + accountId + '"]');
      var open = panel ? panel.hidden : false;
      setPanel(accountId, open);
      collapsed[accountId] = !open;
      saveCollapsed();
    });
  });

  // -------------------------------------------------------------- select all
  each(document.querySelectorAll('form[data-matrix-entry]'), function (form) {
    form.addEventListener('change', function () { refreshCounts(form.dataset.matrixEntry); });
  });

  each(document.querySelectorAll('[data-select-all-study]'), function (control) {
    control.addEventListener('change', function () {
      var form = document.querySelector('form[data-matrix-entry="' + control.dataset.selectAllStudy + '"]');
      if (form) {
        each(form.querySelectorAll('input[data-child="1"]'), function (box) {
          box.checked = control.checked;
        });
      }
      refreshCounts();
    });
  });

  each(document.querySelectorAll('[data-select-all-user]'), function (control) {
    control.addEventListener('change', function () {
      editableChildBoxes(control.dataset.selectAllUser).forEach(function (box) {
        box.checked = control.checked;
      });
      refreshCounts();
    });
  });

  var clearButton = document.querySelector('[data-draft-clear]');
  if (clearButton) {
    clearButton.addEventListener('click', function () {
      each(document.querySelectorAll('form[data-matrix-entry]'), function (form) {
        var visibility = form.querySelector('[name=visibility]');
        if (visibility) { visibility.checked = visibility.dataset.stored === '1'; }
        each(form.querySelectorAll('input[data-child="1"]'), function (box) {
          box.checked = box.dataset.stored === '1';
        });
        form.removeAttribute('data-draft');
        form.removeAttribute('data-draft-stale');
      });
      writeDrafts({});
      refreshCounts();
    });
  }

  // A committed matrix entry is stored on the server, so exactly this entry's
  // draft is done; every other draft stays in the batch (a revision change
  // marks the remaining ones as needing a fresh preview). A successful notice
  // also takes keyboard focus (the real outcome).
  var committed = document.querySelector('[data-committed-kind]');
  if (committed) {
    if (committed.dataset.committedKind === 'matrix' && committed.dataset.committedStudy) {
      var drafts = readDrafts();
      var key = committed.dataset.committedUser + '-' + committed.dataset.committedStudy;
      if (drafts[key]) {
        delete drafts[key];
        writeDrafts(drafts);
        var form = document.querySelector('form[data-matrix-entry="' + key + '"]');
        if (form) {
          form.removeAttribute('data-draft');
          form.removeAttribute('data-draft-stale');
        }
      }
    }
    try { committed.focus(); } catch (error) { /* focus is best effort */ }
  }

  if (matrix) {
    applyDrafts();
    refreshCounts();
  }

  // --------------------------------------------------------------- the dialog
  if (!dialog) { return; }
  var form = dialog.querySelector('[data-confirm-form]');
  var state = dialog.querySelector('[data-confirm-state]');
  var errorBox = dialog.querySelector('[data-confirm-error]');
  var impact = dialog.querySelector('[data-confirm-impact]');
  var usernameRow = dialog.querySelector('[data-confirm-username-row]');
  var submitButton = dialog.querySelector('[data-confirm-submit]');
  var cancelButton = dialog.querySelector('[data-confirm-cancel]');
  var retryButton = dialog.querySelector('[data-confirm-retry]');
  var refreshButton = dialog.querySelector('[data-confirm-refresh]');
  var password = dialog.querySelector('[name=password]');
  var confirmUsername = dialog.querySelector('[name=confirm_username]');
  var lastTrigger = null;
  // The in-flight lock belongs to the request, not to the dialog being open:
  // Escape, a close or a re-open can never unlock a submit that is still in
  // flight, and a late response of an older request is never applied.
  var inFlight = false;
  var flightId = 0;

  function label(name) { return dialog.dataset[name] || ''; }

  function setState(text, kind) {
    if (!state) { return; }
    state.hidden = !text;
    state.textContent = text || '';
    state.className = kind === 'error' ? 'error' : 'muted';
  }

  function setBusy(busy) {
    if (submitButton) {
      submitButton.disabled = busy;
      submitButton.textContent = busy ? label('labelChecking') : label('labelSubmit');
    }
    if (cancelButton) { cancelButton.disabled = busy; }
    if (retryButton) { retryButton.hidden = true; }
    if (refreshButton) { refreshButton.hidden = true; }
  }

  function originField(origin, name) {
    var field = origin ? origin.querySelector('[name=' + name + ']') : null;
    return field ? field.value : '';
  }

  function openDialog(trigger, origin) {
    if (inFlight) { return; }
    lastTrigger = trigger || null;
    if (errorBox) { errorBox.hidden = true; errorBox.textContent = ''; }
    setState('', 'muted');
    if (password) { password.value = ''; }
    if (confirmUsername) { confirmUsername.value = ''; }
    var op = origin ? originField(origin, 'op') : (trigger.dataset.confirmOp || '');
    var username = origin ? originField(origin, 'username') : (trigger.dataset.confirmUsername || '');
    var revisionValue = origin ? originField(origin, 'revision') : (trigger.dataset.revision || '');
    var previewId = origin ? originField(origin, 'preview_id') : (trigger.dataset.previewId || '');
    form.querySelector('[name=op]').value = op;
    form.querySelector('[name=username]').value = username;
    if (revisionValue) { form.querySelector('[name=revision]').value = revisionValue; }
    form.querySelector('[name=preview_id]').value = previewId;
    var deleting = op === 'delete';
    if (impact) {
      impact.hidden = !deleting;
      impact.textContent = deleting ? label('deleteImpact') : '';
    }
    if (usernameRow) { usernameRow.hidden = !deleting; }
    if (confirmUsername) { confirmUsername.required = deleting; }
    if (submitButton) {
      if (deleting) { submitButton.setAttribute('data-danger', '1'); }
      else { submitButton.removeAttribute('data-danger'); }
    }
    setBusy(false);
    if (typeof dialog.showModal === 'function') { dialog.showModal(); }
    if (password) { password.focus(); }
  }

  function closeDialog() {
    // An in-flight submit owns the dialog: closing must not unlock it.
    if (inFlight) { return; }
    if (dialog.open && typeof dialog.close === 'function') { dialog.close(); }
    setBusy(false);
    if (lastTrigger && lastTrigger.nodeType === 1 && document.contains(lastTrigger)) {
      try { lastTrigger.focus(); } catch (error) { /* focus is best effort */ }
    }
    lastTrigger = null;
  }

  each(document.querySelectorAll('[data-confirm-open]'), function (trigger) {
    trigger.addEventListener('click', function () { openDialog(trigger, null); });
  });

  dialog.addEventListener('cancel', function (event) {
    // Escape: close, send nothing, return focus to the trigger. The in-flight
    // lock is checked inside closeDialog.
    event.preventDefault();
    closeDialog();
  });
  if (cancelButton) { cancelButton.addEventListener('click', closeDialog); }
  if (refreshButton) {
    refreshButton.addEventListener('click', function () { window.location.reload(); });
  }

  function requestPayload() { return new FormData(form); }

  function confirmAtServer(previewId) {
    return fetch('/users?preview_status=' + encodeURIComponent(previewId),
                 {credentials: 'same-origin'}).then(function (response) {
      if (!response.ok) { return null; }
      return response.json();
    });
  }

  function replaceDocumentWith(html) {
    if (!html) { window.location.reload(); return; }
    // Show exactly the server-rendered page, like a native form POST, so no
    // client-side text can ever fake a result.
    var doc = document.open('text/html', 'replace');
    doc.write(html);
    doc.close();
  }

  function beginFlight() {
    inFlight = true;
    flightId += 1;
    setState(label('labelChecking'), 'muted');
    setBusy(true);
    return flightId;
  }

  function endFlight(id) {
    // A late response of an older request never unlocks or relabels a newer
    // one, and never turns a still-running submit into a fresh opportunity.
    if (id !== flightId) { return false; }
    inFlight = false;
    setBusy(false);
    return true;
  }

  function handleUnknown(payload, id) {
    var previewId = payload.get('preview_id') || '';
    var check = previewId ? confirmAtServer(previewId) : Promise.resolve(null);
    check.then(function (status) {
      if (id !== flightId) { return; }
      if (status && status.state === 'consumed') {
        setState(label('labelCommitted'), 'muted');
        setBusy(true);
        window.setTimeout(function () { window.location.assign(window.location.pathname); }, 400);
        return;
      }
      if (!endFlight(id)) { return; }
      if (status && status.state === 'pending') {
        // "Not finished at query time": the original request may still be in
        // flight, so this is not a claim that it never ran. A retry replays the
        // same preview and the server re-checks the current authority.
        setState(label('labelPending'), 'muted');
        if (retryButton) { retryButton.hidden = false; }
        if (refreshButton) { refreshButton.hidden = false; }
        return;
      }
      setState(label('labelUnknown'), 'error');
      if (refreshButton) { refreshButton.hidden = false; }
    }, function () {
      if (!endFlight(id)) { return; }
      setState(label('labelUnknown'), 'error');
      if (refreshButton) { refreshButton.hidden = false; }
    });
  }

  function submitPayload(payload, id) {
    fetch('/users', {method: 'POST', credentials: 'same-origin', body: payload})
      .then(function (response) {
        // Reading the body can fail even after the response headers arrived (for
        // example a connection cut mid-body). That is an unknown result and must
        // take the same read-only check, never a success claim.
        return response.text().then(function (text) {
          if (id !== flightId) { return; }
          var type = response.headers.get('Content-Type') || '';
          if (type.indexOf('text/html') !== -1) {
            replaceDocumentWith(text);
            return;
          }
          var code = '';
          try { code = (JSON.parse(text) || {}).code || ''; } catch (error) { code = ''; }
          if (!endFlight(id)) { return; }
          setState(code ? (label('labelRefused') + ' [' + code + ']') : label('labelRefused'), 'error');
        }, function () { handleUnknown(payload, id); });
      }, function () { handleUnknown(payload, id); });
  }

  function submitFromDialog() {
    if (inFlight) { return; }
    if (confirmUsername && confirmUsername.required && !confirmUsername.value.trim()) {
      setState(label('labelRefused'), 'error');
      return;
    }
    var id = beginFlight();
    submitPayload(requestPayload(), id);
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    submitFromDialog();
  });

  if (retryButton) { retryButton.addEventListener('click', submitFromDialog); }

  // Preview-bound confirmation forms (v2 matrix and platform previews) open the
  // dialog from their own real submit button, so cancel/Escape can return the
  // focus to that exact control. The v1 page never renders this dialog and keeps
  // its inline password confirmation.
  each(document.querySelectorAll('form[data-confirm="password"]'), function (origin) {
    // The dialog owns the re-authentication: don't let native constraint
    // validation swallow the submit event before the dialog can open. The
    // server-side password check is unchanged.
    origin.noValidate = true;
    origin.addEventListener('submit', function (event) {
      event.preventDefault();
      openDialog(origin.querySelector('button[type="submit"]') || origin.querySelector('button'), origin);
    });
  });
}());
