"use client";

import React from "react";

interface Props {
  children: React.ReactNode;
}

interface State {
  hasError: boolean;
}

// Страховка: ловит падения React-дерева карты и показывает понятный баннер
// вместо пустого экрана.
export class MapErrorBoundary extends React.Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): Partial<State> {
    return { hasError: true };
  }

  componentDidCatch(error: unknown, info: React.ErrorInfo) {
    console.error("[map] ErrorBoundary caught:", error, info.componentStack);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="absolute inset-x-4 top-1/3 z-30 rounded-2xl bg-red-600/95 px-4 py-3 text-sm text-white shadow-xl">
          Ошибка загрузки карты
        </div>
      );
    }
    return this.props.children;
  }
}
