#!/bin/bash
#PBS -N Nickel_acetate_plus1_doublet_quartet_Freq_MECP_other_main
#PBS -l nodes=1:ppn=12
#PBS -o Nickel_acetate_plus1_doublet_quartet_Freq_MECP_other_main.pbs.out
#PBS -e Nickel_acetate_plus1_doublet_quartet_Freq_MECP_other_main.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Nickel_acetate_plus1_doublet_quartet_Freq_MECP_other_main"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Nickel_acetate_plus1_doublet_quartet_Freq_MECP_other_main.inp > Nickel_acetate_plus1_doublet_quartet_Freq_MECP_other_main.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
