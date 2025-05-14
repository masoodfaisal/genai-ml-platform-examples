# # https://modelcontextprotocol.io/quickstart/server

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("Tax_Information_Validator", host="0.0.0.0", port=5000)

@mcp.tool()
async def validate_tax_invoice_number(invoice_number: str):
    """ Validates the tax invoice number and returns if it is valid or invalid """
    
    print("MCP: validate_tax_invoice_number")
    if len(invoice_number) != 10:
        return  f"Invalid invoice number {invoice_number}. External Data validation failed.",
            
    # Check if the invoice number is valid in the system
    # This is a placeholder for the actual validation logic
    # if invoice_number not in ["1234567890", "0987654321"]:
    #     return f"Invalid invoice number in the system {invoice_number}"
    return f"Valid invoice number format and in the system {invoice_number}"

if __name__ == "__main__":
    mcp.run(transport="sse")
    
    
