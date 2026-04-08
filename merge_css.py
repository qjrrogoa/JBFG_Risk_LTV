import sys

# Read enterprise css
css = open('frontend/src/index_enterprise.css', 'r', encoding='utf-8').read()

theme_css = '''
@theme {
  /* 모든 텍스트 클래스 정확히 1.3배로 확대 (폰트 크기 및 줄간격) */
  --text-xs: 0.975rem;
  --text-xs--line-height: 1.3rem;
  --text-sm: 1.1375rem;
  --text-sm--line-height: 1.625rem;
  --text-base: 1.3rem;
  --text-base--line-height: 1.95rem;
  --text-lg: 1.4625rem;
  --text-lg--line-height: 2.275rem;
  --text-xl: 1.625rem;
  --text-xl--line-height: 2.275rem;
  --text-2xl: 1.95rem;
  --text-2xl--line-height: 2.6rem;
  --text-3xl: 2.4375rem;
  --text-3xl--line-height: 2.925rem;
  --text-4xl: 2.925rem;
  --text-4xl--line-height: 3.25rem;
}
'''

new_css = css.replace('@import "tailwindcss";', '@import "tailwindcss";\n' + theme_css)
open('frontend/src/index.css', 'w', encoding='utf-8').write(new_css)
