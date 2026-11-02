using System;
using System.Collections.Generic;
using System.Configuration;
using System.IO;
using System.Linq;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Web;
using System.Web.Script.Serialization;

// Runs after Windows Authentication. A Rewrite serverVariable evaluated at
// BeginRequest cannot reliably provide LOGON_USER and must not carry identity.
namespace DataPlatform
{
    public sealed class SopIdentityBridge : IHttpModule
    {
        public sealed class BridgeConfig
        {
            public string[] WriteGroups { get; set; }
            public string ProxySecretPath { get; set; }
            public string PublicOrigin { get; set; }
        }

        public void Init(HttpApplication app)
        {
            app.BeginRequest += StripUntrustedHeaders;
            app.PostAuthenticateRequest += Authenticate;
        }

        private static void StripUntrustedHeaders(object sender, EventArgs e)
        {
            HttpContext context = ((HttpApplication)sender).Context;
            context.Request.Headers.Remove("X-SOP-Proxy-Key");
            context.Request.Headers.Remove("X-SOP-User");
        }

        private static void Deny(HttpContext context, int code, string message)
        {
            context.Response.StatusCode = code;
            context.Response.TrySkipIisCustomErrors = true;
            context.Response.ContentType = "application/json; charset=utf-8";
            context.Response.Cache.SetCacheability(HttpCacheability.NoCache);
            context.Response.Write(new JavaScriptSerializer().Serialize(new { error = message }));
            context.ApplicationInstance.CompleteRequest();
        }

        private static void Authenticate(object sender, EventArgs e)
        {
            HttpContext context = ((HttpApplication)sender).Context;
            try
            {
                // Never accept an identity merely because a client supplied headers.
                WindowsIdentity identity = context.User == null ? null : context.User.Identity as WindowsIdentity;
                if (identity == null || !identity.IsAuthenticated || identity.IsAnonymous || String.IsNullOrWhiteSpace(identity.Name))
                {
                    Deny(context, 401, "Windows authentication is required.");
                    return;
                }
                string path = ConfigurationManager.AppSettings["SopProxyConfigPath"];
                if (String.IsNullOrWhiteSpace(path) || !Path.IsPathRooted(path))
                    throw new ConfigurationErrorsException("Missing proxy configuration.");
                BridgeConfig config = new JavaScriptSerializer().Deserialize<BridgeConfig>(File.ReadAllText(path, Encoding.UTF8));
                if (config == null || config.WriteGroups == null || !Path.IsPathRooted(config.ProxySecretPath ?? ""))
                    throw new ConfigurationErrorsException("Invalid proxy configuration.");
                Uri origin;
                if (!Uri.TryCreate(config.PublicOrigin, UriKind.Absolute, out origin) ||
                    (origin.Scheme != "http" && origin.Scheme != "https") || origin.AbsolutePath != "/" ||
                    !String.IsNullOrEmpty(origin.Query) || !String.IsNullOrEmpty(origin.Fragment) || !String.IsNullOrEmpty(origin.UserInfo))
                    throw new ConfigurationErrorsException("Invalid public origin.");
                if (context.Request.HttpMethod != "GET" && context.Request.HttpMethod != "HEAD" && context.Request.HttpMethod != "POST")
                {
                    Deny(context, 405, "HTTP method is not supported.");
                    return;
                }
                if (context.Request.HttpMethod == "POST")
                {
                    string requestOrigin = context.Request.Headers["Origin"];
                    if (!String.Equals(requestOrigin, config.PublicOrigin.TrimEnd('/'), StringComparison.OrdinalIgnoreCase))
                    {
                        Deny(context, 403, "Request origin is not permitted.");
                        return;
                    }
                    WindowsPrincipal principal = new WindowsPrincipal(identity);
                    if (!config.WriteGroups.Any(group => !String.IsNullOrWhiteSpace(group) && principal.IsInRole(group)))
                    {
                        Deny(context, 403, "This account has read-only SOP access.");
                        return;
                    }
                }
                string secret = File.ReadAllText(config.ProxySecretPath, Encoding.UTF8).Trim();
                if (!Regex.IsMatch(secret, "^[A-Za-z0-9_-]{32,256}$", RegexOptions.CultureInvariant))
                    throw new ConfigurationErrorsException("Invalid proxy secret.");
                if (identity.Name.Length > 100 || identity.Name.Any(c => c < 32 || c == 127))
                {
                    Deny(context, 403, "The Windows account identifier is not supported by the identity bridge.");
                    return;
                }
                context.Request.Headers.Set("X-SOP-Proxy-Key", secret);
                context.Request.Headers.Set("X-SOP-User", "b64:" + Convert.ToBase64String(Encoding.UTF8.GetBytes(identity.Name)));
            }
            catch
            {
                // Do not include filesystem paths, account details or keys in errors.
                Deny(context, 503, "SOP identity bridge is not configured. Contact the administrator.");
            }
        }

        public void Dispose() { }
    }
}
