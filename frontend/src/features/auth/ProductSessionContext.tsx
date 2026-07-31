import { createContext, useContext, type ReactNode } from "react";
import type { ProductSession } from "./authContract";

const ProductSessionContext = createContext<ProductSession | null>(null);

export function ProductSessionProvider({
  children,
  session,
}: {
  children: ReactNode;
  session: ProductSession;
}) {
  return (
    <ProductSessionContext.Provider value={session}>
      {children}
    </ProductSessionContext.Provider>
  );
}

export function useOptionalProductSession(): ProductSession | null {
  return useContext(ProductSessionContext);
}
