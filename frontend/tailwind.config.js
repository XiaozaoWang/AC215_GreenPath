import colors from './src/styles/colors';
import accent from './src/styles/colors/accent';
import background from './src/styles/colors/background';
import blur from './src/styles/colors/blur';
import brand from './src/styles/colors/brand';
import surface from './src/styles/colors/surface';
import action from './src/styles/colors/action';
import border from './src/styles/colors/border';
import status from './src/styles/colors/status';
import text from './src/styles/colors/text';
import shadow from './src/styles/colors/shadow';
import boxShadow from './src/styles/boxShadow';
import typography from './src/styles/typography';
import buttons from './src/styles/buttons';
import caret from './src/styles/caret';
import tailwindColors from 'tailwindcss/colors';

/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './src/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    colors: {
      raw: {
        ...colors,
      },
      accent,
      background,
      blur,
      brand,
      surface,
      action,
      border,
      status,
      text,
      shadow,
      // Tailwind defaults used by the chat and header components
      gray: tailwindColors.gray,
      blue: tailwindColors.blue,
      red: tailwindColors.red,
      amber: tailwindColors.amber,
      // SmartPath theme colors: a soft purple→pink gradient for emphasis, teal/coral red for positive/negative scores
      emerald: tailwindColors.emerald,
      violet: tailwindColors.violet,
      pink: tailwindColors.pink,
      teal: tailwindColors.teal,
      slate: tailwindColors.slate,
      black: '#000000',
      white: '#ffffff',
      transparent: 'transparent',
      inherit: 'inherit',
    },
    boxShadow,
    opacity: {
      0: '0',
      16: '0.16',
      40: '0.4',
      50: '0.5',
      64: '0.64',
      100: '1',
    },
    zIndex: {
      0: '0',
      1: '1',
      2: '2',
      3: '3',
      rank: '30',
      header: '75',
      postNavigation: '76',
      sidebar: '70',
      sidebarOverlay: '77',
      tooltip: '80',
      popup: '90',
      modal: '100',
      max: '1000',
      '-1': '-1',
    },
    fontFamily: {
      sans: [
        'system-ui',
        '-apple-system',
        'BlinkMacSystemFont',
        'Roboto',
        'Helvetica',
        'Ubuntu',
        'Segoe UI',
        'Arial',
        'sans-serif',
        'Apple Color Emoji',
        'Segoe UI Emoji',
        'Segoe UI Symbol',
      ],
    },
    screens: {
      mobileL: '420px',
      mobileXL: '500px',
      mobileXXL: '550px',
      tablet: '656px',
      laptop: '1020px',
      laptopL: '1360px',
      laptopXL: '1668px',
      desktop: '1976px',
      desktopL: '2156px',
      mouse: { raw: '(pointer: fine)' },
      responsiveModalBreakpoint: '420px',
    },
    borderRadius: {
      none: '0',
      full: '100%',
      '1/2': '50%',
      2: '0.125rem',
      3: '0.1875rem',
      4: '0.25rem',
      6: '0.375rem',
      8: '0.5rem',
      10: '0.625rem',
      12: '0.75rem',
      14: '0.875rem',
      16: '1rem',
      18: '1.125rem',
      22: '1.375rem',
      24: '1.5rem',
      26: '1.625rem',
      32: '2rem',
      40: '2.5rem',
      48: '3rem',
      50: '3.125rem',
    },
    extend: {
      // Tailwind's default radius/shadow scales (the theme above replaces them)
      // Note: rounded-full is 100% here (circles); use rounded-pill for pill shapes
      borderRadius: {
        sm: '0.125rem',
        DEFAULT: '0.25rem',
        md: '0.375rem',
        lg: '0.5rem',
        xl: '0.75rem',
        '2xl': '1rem',
        '3xl': '1.5rem',
        pill: '9999px',
      },
      boxShadow: {
        xs: '0 1px 2px 0 rgb(0 0 0 / 0.04)',
        sm: '0 1px 2px 0 rgb(0 0 0 / 0.05)',
        DEFAULT: '0 1px 3px 0 rgb(0 0 0 / 0.1), 0 1px 2px -1px rgb(0 0 0 / 0.1)',
        md: '0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)',
        lg: '0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1)',
      },
      maxHeight: {
        'img-desktop': '400px',
        'img-mobile': '280px',
        'rank-modal': 'calc(100vh - 5rem)',
        page: 'calc(100vh - 4rem)',
        commentBox: '18.25rem',
        card: '24rem',
        cardLarge: '27rem',
      },
      minHeight: {
        page: 'calc(100vh - 4rem)',
        card: '24rem',
      },
      gap: {
        unset: 'unset',
      },
      opacity: {
        24: '0.24',
        32: '0.32',
      },
      inset: {
        'screen-20': '20vh',
        'screen-40': '40vh',
        'screen-60': '60vh',
        'screen-80': '80vh',
        screen: '100vh',
      },
      spacing: {
        18: '4.5rem',
        22: '5.5rem',
        26: '6.5rem',
        70: '17.5rem',
      },
      maxWidth: {
        widget: '19.25rem',
      },
      width: {
        70: '17.5rem',
      },
      height: {
        logo: '1.125rem',
        'logo-big': '2.0625rem',
        50: '12.5rem',
      },
      blur: {
        20: '1.25rem',
      },
      keyframes: {
        'scale-down-pulse': {
          '0%, 100%': { transform: 'scale(1)', opacity: '1' },
          '50%': { transform: 'scale(0.7)', opacity: '0.5' },
        },
      },
      animation: {
        'scale-down-pulse':
          'scale-down-pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      },
    },
  },
  plugins: [caret, typography, buttons],
}

