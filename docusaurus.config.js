// @ts-check
// Docusaurus configuration for the World Gal-Game engine documentation site.
// Docs-only: the existing flat docs/*.md tree is the content source. We do NOT
// move or rename any doc; the sidebar is hand-authored in sidebars.js.

const {themes: prismThemes} = require('prism-react-renderer');

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: 'World Gal-Game',
  tagline: 'AI 原生的 pygame 視覺小說 / Gal-Game 引擎',
  favicon: 'img/favicon.svg',

  // Production URL and base path for GitHub Pages.
  url: 'https://treeleaves30760.github.io',
  baseUrl: '/world-gal-game/',
  trailingSlash: false,

  // GitHub Pages deployment config.
  organizationName: 'treeleaves30760',
  projectName: 'world-gal-game',

  // The docs cross-link each other and link out to ../ROADMAP.md / ../CLAUDE.md
  // which live outside the site. Warn (don't fail the build) on broken links.
  // (onBrokenMarkdownLinks lives under markdown.hooks below — its top-level form
  // is deprecated in Docusaurus 3.10+ and removed in v4.)
  onBrokenLinks: 'warn',

  // Content is intentionally mixed English / Traditional Chinese. No i18n split.
  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  // The existing docs are authored as plain Markdown (CommonMark), not MDX:
  // they contain literal `{...}` (e.g. dict shapes in tables) and `<...>` that
  // the MDX parser would misread as JSX. `format: 'detect'` routes `.md` files
  // through the CommonMark parser (and would still give any future `.mdx` the
  // MDX parser). This is the seam that lets us keep docs/ unedited and flat.
  markdown: {
    format: 'detect',
    hooks: {
      // Warn (don't fail) on unresolved Markdown links — many docs point at
      // ../ROADMAP.md / ../CLAUDE.md / ../AGENTS.md, which live outside the site.
      onBrokenMarkdownLinks: 'warn',
    },
  },

  presets: [
    [
      'classic',
      /** @type {import('@docusaurus/preset-classic').Options} */
      ({
        docs: {
          path: 'docs',
          routeBasePath: '/',
          sidebarPath: require.resolve('./sidebars.js'),
          editUrl:
            'https://github.com/treeleaves30760/world-gal-game/tree/main/docs/',
        },
        blog: false,
        theme: {
          customCss: require.resolve('./src/css/custom.css'),
        },
      }),
    ],
  ],

  themeConfig:
    /** @type {import('@docusaurus/preset-classic').ThemeConfig} */
    ({
      colorMode: {
        defaultMode: 'dark',
        respectPrefersColorScheme: true,
      },
      navbar: {
        title: 'World Gal-Game',
        logo: {
          alt: 'World Gal-Game',
          src: 'img/logo.svg',
          // Hide gracefully if the logo asset is absent.
          width: 32,
          height: 32,
        },
        items: [
          {
            type: 'docSidebar',
            sidebarId: 'docsSidebar',
            position: 'left',
            label: '文件 Docs',
          },
          {
            href: 'https://github.com/treeleaves30760/world-gal-game',
            label: 'GitHub',
            position: 'right',
          },
        ],
      },
      footer: {
        style: 'dark',
        links: [
          {
            title: '文件 Docs',
            items: [
              {
                label: '入門 Getting Started',
                to: '/getting-started',
              },
              {
                label: 'AI 原生開發',
                to: '/ai-developer-guide',
              },
            ],
          },
          {
            title: '專案 Project',
            items: [
              {
                label: 'GitHub',
                href: 'https://github.com/treeleaves30760/world-gal-game',
              },
              {
                label: 'Issues',
                href: 'https://github.com/treeleaves30760/world-gal-game/issues',
              },
            ],
          },
        ],
        copyright: `Copyright © ${new Date().getFullYear()} World Gal-Game. Built with Docusaurus.`,
      },
      prism: {
        theme: prismThemes.github,
        darkTheme: prismThemes.dracula,
        additionalLanguages: ['python', 'bash', 'yaml', 'json'],
      },
    }),
};

module.exports = config;
