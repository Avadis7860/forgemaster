// queryClient.ts — client React Query partagé (lectures ponctuelles REST de l'état serveur-autoritatif). Le
// canal WebSocket d'écho (`GET /ws`, cf. server/index.ts) POUSSE l'état à chaque tick ; React Query sert les
// requêtes ponctuelles (santé, listes). Étends les options par défaut (staleTime, retry) au besoin. Le client
// ne calcule JAMAIS l'état de jeu (anti-triche, décision verrouillée 2) — il lit et propose des commandes.
import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient();
