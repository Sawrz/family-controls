import { sveltekit } from '@sveltejs/kit/vite';
import adapter from '@sveltejs/adapter-static';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [
    sveltekit({
      adapter: adapter({ fallback: '200.html' }),
      csp: {
        mode: 'hash',
        directives: {
          'default-src': ['self'],
          'script-src': ['self'],
          'style-src': ['self'],
          'connect-src': ['self'],
          'object-src': ['none'],
          'base-uri': ['none'],
          'form-action': ['self'],
        },
      },
    }),
  ],
  build: { sourcemap: true },
});
