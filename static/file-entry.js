/* The interface needs its local API, even when index.html is opened directly. */
if (window.location.protocol === 'file:') {
  window.location.replace('http://127.0.0.1:8767/' + window.location.hash);
}
