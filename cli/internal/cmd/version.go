package cmd

import (
	"fmt"

	"github.com/spf13/cobra"
)

func newVersionCmd(d *Deps, g *globalFlags) *cobra.Command {
	return &cobra.Command{
		Use:   "version",
		Short: "CLI 버전",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			if g.output == "json" {
				fmt.Fprintf(d.Stdout, "{\"version\":%q}\n", version)
				return nil
			}
			fmt.Fprintf(d.Stdout, "kodeploy %s\n", version)
			return nil
		},
	}
}
