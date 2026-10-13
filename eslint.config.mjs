import tseslint from 'typescript-eslint';
export default tseslint.config({
  files: ['web/src/**/*.ts'],
  languageOptions: {parser: tseslint.parser, parserOptions: {project: './tsconfig.json'}},
  plugins: {'@typescript-eslint': tseslint.plugin},
  rules: {'no-debugger': 'error', 'no-unreachable': 'error', 'no-constant-condition': 'error',
    '@typescript-eslint/no-floating-promises': 'error'}
});
