import { defineConfig } from 'vitepress'
import { withMermaid } from 'vitepress-plugin-mermaid'

// https://vitepress.dev/reference/site-config
//
// A documentation site over the existing documentation/ pages — a curated
// sidebar, local search, and the duckdb-vectors house skin. No blog
// machinery: no state filter, no Atom feed, no post components.
export default withMermaid(
  defineConfig({
    title: 'duckdb-vectors',
    description:
      'A frugal, embeddable semantic-search building block for agentic systems.',
    // Served from the project Pages URL bernard357.github.io/duckdb-vectors/.
    // If a custom domain is added later, this flips to '/'.
    base: '/duckdb-vectors/',
    lang: 'en-US',
    cleanUrls: true,
    lastUpdated: true,
    // Pre-bundle Mermaid's CommonJS dependencies so the Vite dev server
    // resolves their ESM interop; without this the dev page can render blank.
    // The production (Rollup) build already handles this.
    vite: {
      optimizeDeps: {
        include: ['mermaid', 'dayjs', 'fastdom', 'cytoscape'],
      },
    },
    themeConfig: {
      // https://vitepress.dev/reference/default-theme-config
      nav: [
        { text: 'PyPI', link: 'https://pypi.org/project/duckdb-vectors/' },
        {
          text: 'GitHub',
          link: 'https://github.com/bernard357/duckdb-vectors',
        },
      ],
      sidebar: [
        {
          text: 'Start here',
          items: [
            { text: 'User guide', link: '/duckdb-vectors-guide' },
            { text: 'Technical design', link: '/duckdb-vectors-primitive' },
            { text: 'How DuckDB is used', link: '/duckdb-workbook' },
            { text: 'Developer workbook', link: '/developer-workbook' },
          ],
        },
        {
          text: 'Use cases',
          items: [
            { text: 'Set up a RAG agent', link: '/setup-rag-procedure' },
            {
              text: 'Turn an LLM wiki into RAG',
              link: '/setup-llm-wiki-to-rag-procedure',
            },
            {
              text: 'Search over skills',
              link: '/setup-skills-search-procedure',
            },
          ],
        },
        {
          text: 'Harnessing guides',
          items: [
            { text: 'Containerized agent', link: '/containerized-agent-guide' },
            { text: 'Docker Agent', link: '/docker-agent-guide' },
          ],
        },
        {
          text: 'Deployment guides',
          items: [
            { text: 'AWS Lambda', link: '/aws-lambda-guide' },
            {
              text: 'AWS Bedrock AgentCore',
              link: '/aws-bedrock-agentcore-guide',
            },
          ],
        },
      ],
      search: {
        provider: 'local',
      },
      socialLinks: [
        {
          icon: 'github',
          link: 'https://github.com/bernard357/duckdb-vectors',
        },
      ],
    },
  }),
)
