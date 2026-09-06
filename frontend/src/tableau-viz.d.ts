// Tableau Embedding API v3 웹 컴포넌트 타입 선언
import type * as React from 'react';

declare module 'react/jsx-runtime' {
  namespace JSX {
    interface IntrinsicElements {
      'tableau-viz': React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement> & {
        src?: string;
        toolbar?: 'top' | 'bottom' | 'hidden';
        'hide-tabs'?: boolean;
        token?: string;
      };
    }
  }
}
