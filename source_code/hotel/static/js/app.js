// Small progressive enhancements. Every page also works without JavaScript.
document.addEventListener('submit', function (e) {
  var f = e.target.closest('form[data-confirm]');
  if (f && !window.confirm(f.dataset.confirm)) e.preventDefault();
});
document.addEventListener('click', function (e) {
  var b = e.target.closest('[data-print]');
  if (b) { e.preventDefault(); window.print(); }
  var c = e.target.closest('[data-copy]');
  if (c && navigator.clipboard) { navigator.clipboard.writeText(c.dataset.copy); c.textContent = 'Copied'; }
});
document.addEventListener('change', function (e) {
  var t = e.target.closest('[data-autosubmit]');
  if (t && t.form) t.form.submit();
});
