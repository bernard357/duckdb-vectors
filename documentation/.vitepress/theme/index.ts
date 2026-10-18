import DefaultTheme from 'vitepress/theme'
import type { Theme } from 'vitepress'
import { h } from 'vue'
import SiteFooter from './SiteFooter.vue'
import './custom.css'

// Extend the VitePress default theme with the duckdb-vectors house skin.
// Keeping the default theme (rather than forking it) means the brand lives
// entirely in custom.css and survives VitePress upgrades. Slot:
//   layout-bottom — two-part site footer (SiteFooter)
export default {
  extends: DefaultTheme,
  Layout() {
    return h(DefaultTheme.Layout, null, {
      'layout-bottom': () => h(SiteFooter),
    })
  },
} satisfies Theme
