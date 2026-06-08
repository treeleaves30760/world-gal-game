// @ts-check
// Hand-authored sidebar. The doc tree under ../docs is flat; we impose structure
// here. Each string is a doc id (filename without .md). README is the site
// landing page (slug "/") and sits at the top, outside any category.
//
// If you add a new doc to ../docs, add its id here too — autogeneration is
// intentionally NOT used, so new files are otherwise invisible in the sidebar.

/** @type {import('@docusaurus/plugin-content-docs').SidebarsConfig} */
const sidebars = {
  docsSidebar: [
    'README',
    {
      type: 'category',
      label: '入門 Getting Started',
      collapsed: false,
      items: ['getting-started', 'tutorial-build-a-game'],
    },
    {
      type: 'category',
      label: '製作遊戲 Authoring a Pack',
      items: [
        'pack-format',
        'scenes',
        'characters',
        'locations',
        'items',
        'quests',
        'resources',
        'shops',
        'achievements',
        'affection',
        'theme-and-locale',
        'presentation-and-extras',
        'cookbook',
      ],
    },
    {
      type: 'category',
      label: '引擎概念 Engine Concepts',
      items: ['architecture', 'galgame-maturity'],
    },
    {
      type: 'category',
      label: '參考 Reference',
      items: ['effects-reference', 'conditions-reference'],
    },
    {
      type: 'category',
      label: 'AI 原生開發 AI-Native Development',
      items: [
        'ai-developer-guide',
        'ai-native-contract',
        'ai-native-world-model',
        'headless',
        'session-protocol',
        'ai-debug',
      ],
    },
    {
      type: 'category',
      label: '外掛 Plugins',
      items: ['plugins'],
    },
    {
      type: 'category',
      label: '發佈 Distribution',
      items: [
        'distribution',
        'distribution-web',
        'distribution-steam',
        'distribution-mobile',
        'art-transparency',
      ],
    },
  ],
};

module.exports = sidebars;
