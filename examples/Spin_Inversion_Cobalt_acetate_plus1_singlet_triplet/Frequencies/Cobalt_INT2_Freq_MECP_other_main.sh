#!/bin/bash
#PBS -N Cobalt_INT2_Freq_MECP_other_main
#PBS -l nodes=1:ppn=12
#PBS -o Cobalt_INT2_Freq_MECP_other_main.pbs.out
#PBS -e Cobalt_INT2_Freq_MECP_other_main.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Cobalt_INT2_Freq_MECP_other_main"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Cobalt_INT2_Freq_MECP_other_main.inp > Cobalt_INT2_Freq_MECP_other_main.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
