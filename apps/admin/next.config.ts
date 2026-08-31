import type { NextConfig } from "next";

/**
 * The back-office UI. Its own app, on its own port, deliberately.
 *
 * It could have been a route group inside `@mishne/web`. It is not, because
 * that ships admin markup in the bundle every customer downloads and puts the
 * admin screens on the origin the product's session cookie is scoped to. The
 * whole design of the back-office is that it is a different thing on a
 * different door; a shared Next app would undo that at the last step.
 *
 * It is also not meant to be deployed publicly at all: the API it talks to
 * binds to loopback and refuses otherwise, so this runs where you can reach
 * that — your machine, or behind the VPN.
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,
};

export default nextConfig;
