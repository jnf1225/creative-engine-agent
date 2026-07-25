// MIGRATION REPLACEMENT for the Lovable-generated OAuth broker.
//
// The original file wrapped @lovable.dev/cloud-auth-js (Lovable's hosted OAuth
// broker) and returned Supabase session tokens. Off Lovable that broker is gone,
// so this drop-in reimplements the SAME `lovable.auth.signInWithOAuth(...)`
// surface using **native Supabase OAuth**, which means:
//   - call sites (auth.tsx, share.$token.tsx) need NO changes; and
//   - the OAuth client is now YOUR OWN Google client configured in Supabase
//     Auth → Providers → Google (enable "email"-based identity linking so
//     returning users attach to their migrated auth.users row).
//
// Contract preserved: returns { redirected: true } when the browser is being
// sent to Google, or { error } on failure. Supabase performs the full-page
// redirect itself (skipBrowserRedirect: false), matching the old broker's
// `result.redirected` early-return behavior.

import { supabase } from "../supabase/client";

type SignInOptions = {
  redirect_uri?: string;
  extraParams?: Record<string, string>;
};

type OAuthProvider = "google" | "apple" | "microsoft" | "lovable";

// Map the old provider names to Supabase provider ids. "lovable" is dropped;
// "microsoft" maps to Supabase's "azure".
function toSupabaseProvider(provider: OAuthProvider): "google" | "apple" | "azure" {
  if (provider === "microsoft") return "azure";
  if (provider === "apple") return "apple";
  return "google";
}

export const lovable = {
  auth: {
    signInWithOAuth: async (provider: OAuthProvider, opts?: SignInOptions) => {
      try {
        const { data, error } = await supabase.auth.signInWithOAuth({
          provider: toSupabaseProvider(provider),
          options: {
            redirectTo: opts?.redirect_uri,
            queryParams: opts?.extraParams,
            skipBrowserRedirect: false,
          },
        });
        if (error) return { error };
        // supabase-js has begun the browser redirect to the provider.
        return { redirected: true, url: data?.url };
      } catch (e) {
        return { error: e instanceof Error ? e : new Error(String(e)) };
      }
    },
  },
};
