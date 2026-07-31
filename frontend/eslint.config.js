import js from '@eslint/js'
import reactHooks from 'eslint-plugin-react-hooks'
import globals from 'globals'
import tseslint from 'typescript-eslint'

const ownedTypeScriptFiles = [
  'src/**/*.{ts,tsx}',
]

const reactHookRules = Object.fromEntries(
  Object.entries(reactHooks.configs.flat['recommended-latest'].rules).map(
    ([ruleName, ruleConfig]) => [
      ruleName,
      Array.isArray(ruleConfig) ? ['error', ...ruleConfig.slice(1)] : 'error',
    ],
  ),
)

export default tseslint.config(
  {
    ignores: [
      'dist/**',
      'node_modules/**',
    ],
  },
  {
    name: 'owned-typescript',
    files: ownedTypeScriptFiles,
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: globals.browser,
    },
    linterOptions: {
      reportUnusedDisableDirectives: 'error',
      reportUnusedInlineConfigs: 'error',
    },
    plugins: {
      'react-hooks': reactHooks,
    },
    rules: {
      ...reactHookRules,
      'no-unused-vars': 'off',
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/no-unused-vars': [
        'error',
        {
          argsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
          destructuredArrayIgnorePattern: '^_',
          ignoreRestSiblings: true,
          varsIgnorePattern: '^_',
        },
      ],
    },
  },
  {
    name: 'design-guard',
    files: ['scripts/product-design-guard.mjs'],
    extends: [js.configs.recommended],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    linterOptions: {
      reportUnusedDisableDirectives: 'error',
      reportUnusedInlineConfigs: 'error',
    },
  },
)
