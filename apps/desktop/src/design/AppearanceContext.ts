import { createContext, useContext } from "react";
import { normalizeAppearance } from "../state/appearance.ts";
export const AppearanceContext = createContext(normalizeAppearance(null));
export const useAppearance = () => useContext(AppearanceContext);
