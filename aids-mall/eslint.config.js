import js from '@eslint/js'
import prettier from 'eslint-config-prettier'
import pluginVue from 'eslint-plugin-vue'
import tseslint from 'typescript-eslint'

/**
 * ESLint 9 flat config。
 *
 * 分工原则：**格式归 Prettier，ESLint 只管正确性**。因此显式关掉一批
 * 纯排版类规则（缩进/换行/属性顺序/自闭合/属性数上限）——两边同时管格式时，
 * `npm run lint` 与 `npm run format` 会互相打架，最后没人再跑它们。
 *
 * `no-undef` 关掉：TypeScript 已经做了未定义检查，而 ESLint 的 `no-undef`
 * 需要额外维护 globals 清单（`window`/`localStorage` 会误报），属于重复劳动。
 */
export default tseslint.config(
  { ignores: ['dist/**', 'node_modules/**', 'coverage/**'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  {
    files: ['**/*.vue'],
    languageOptions: {
      parserOptions: { parser: tseslint.parser },
    },
  },
  {
    rules: {
      'no-undef': 'off',
      'vue/multi-word-component-names': 'off',
      // ---- 排版类：交给 Prettier ----
      'vue/max-attributes-per-line': 'off',
      'vue/singleline-html-element-content-newline': 'off',
      'vue/html-self-closing': 'off',
      'vue/attributes-order': 'off',
      'vue/html-indent': 'off',
      'vue/html-closing-bracket-newline': 'off',
    },
  },
  prettier,
)
