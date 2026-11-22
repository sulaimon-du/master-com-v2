// Counter "n / total" for the collection gallery. Alpine CSP build: registered here, used as x-data="gallery".
// Without JS the page still works: the server renders "1 / N" and the thumbnails are plain anchors.
document.addEventListener("alpine:init", () => {
  window.Alpine.data("gallery", () => ({
    current: 1,
    total: 0,
    init() {
      this.total = this.$refs.strip.children.length;
    },
    onScroll() {
      const strip = this.$refs.strip;
      const width = strip.clientWidth || 1;
      this.current = Math.min(this.total, Math.max(1, Math.round(strip.scrollLeft / width) + 1));
    },
    get label() {
      return this.current + " / " + this.total;
    },
  }));
});
